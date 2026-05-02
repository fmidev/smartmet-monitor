"""Full-screen interactive flamegraph panel.

Layout (top to bottom):
    row 0           header (PID + perf status + breadcrumb of zoom path)
    rows 1..K       flame tree (K = depth of currently zoomed subtree)
    row K+1         divider
    rows K+2..h-2   top-symbols list, filling the rest of the screen
    row h-1         footer (key hints)

The previous implementation only used the flame's own depth and left
the lower rows blank. Production stacks are typically 8–15 frames
deep on a 40-row terminal, so half the screen was wasted.

The view is interactive:
    ←/→     previous / next sibling at the same depth
    ↑       parent (same as zooming out by one level)
    ↓       most-used child of the selected frame
    Enter   zoom into the selected frame (it becomes the new root)
    Esc/u   zoom out one level
    0/Home  zoom all the way out
    n/N     next / previous smartmetd PID (same as Proc panel)
    o       toggle on-CPU ↔ off-CPU flamegraph (stacks weighted by
            microseconds blocked; needs bcc-tools / perf access)
"""

from __future__ import annotations

import curses
from typing import Dict, List, Optional, Tuple

from .. import theme
from ..sources.analyze import Finding, SEV_HIGH, SEV_MED, analyze
from ..sources.smartmet_filter import (
    THREAD_CLASS_ALL,
    THREAD_CLASS_BACKGROUND,
    THREAD_CLASS_REQUEST,
    collapse_to_smartmet,
    is_request_stack,
    keep_for_thread_class,
)
from ..state.store import ProcInfo
from ..widgets.bars import human_count, sparkline
from .base import Panel, safe_addstr, write_label, write_row
from .proc import _build_flame_tree, _flame_color, draw_pid_selector


# Preset record durations offered by the `s` selection overlay. Picked
# to span "barely visible flame" through "full investigative dump":
# 1s = ~10% duty (gentlest), 3s = default, 5s noticeably denser,
# 10/20/30 are for focused debugging where the operator accepts the
# overhead.
PERF_SECONDS_PRESETS = (1, 3, 5, 10, 20, 30)


# Valid Flame view modes. Selection is direct via the
# C/B/L/M/W/I/A keys in the panel's handle_key — no cycling.
# Order here is the logical workflow ordering used by the
# README's diagrams: where is the CPU going → where are
# threads stuck → narrow to lock contention → where do faults
# land → who is doing the unblocking → where do block-I/O
# requests originate → which code path allocates memory.
_MODES = ("on-cpu", "off-cpu", "off-cpu-locks", "pagefault",
          "wakeup", "blockflame", "malloc")


# Substring patterns that mark a stack leaf as lock-related. Used by
# the off-cpu-locks filter. We match on substring rather than equality
# because glibc / pthreads frame names vary across kernels and libc
# versions (`__pthread_mutex_lock` vs `pthread_mutex_lock` vs
# `__lll_lock_wait`, etc.); the substrings here cover all the common
# spellings.
_LOCK_LEAF_PATTERNS = (
    "futex_wait", "futex_q", "do_futex",        # kernel
    "__lll_lock", "__lll_unlock",                # glibc low-level lock
    "pthread_mutex", "pthread_cond",
    "pthread_rwlock", "pthread_spin",
    "__pthread_mutex", "__pthread_cond",
)


def _is_lock_stack(stack) -> bool:
    """True when the stack's leaf frame looks like a lock wait.

    Used to filter the off-CPU stack ring down to mutex/futex/cond
    contention only. Walking just the leaf is intentional: the *kind*
    of off-CPU event is determined by the kernel function the thread
    parked in, and that's always the leaf.
    """
    if not stack:
        return False
    leaf = stack[-1]
    return any(p in leaf for p in _LOCK_LEAF_PATTERNS)


def _apply_filters(items, thread_class: str, smartmet_only: bool):
    """Drop / collapse stacks per the active SmartMet-only and thread
    filters. Accepts both shapes the flame tree builder accepts:

      * bare stack tuples (on-CPU, page-fault, wakeup, block-I/O)
      * `(stack, weight)` 2-tuples (off-CPU µs blocked; malloc bytes)

    Returns the same shape, with the SmartMet-only collapse applied to
    the stack component when enabled. The two filters compose: the
    thread filter runs on the *original* stack so the request-entry
    symbol is still visible, then the SmartMet collapse drops any
    non-SmartMet frames.
    """
    out = []
    for item in items:
        if (isinstance(item, tuple) and len(item) == 2
                and isinstance(item[0], tuple)
                and isinstance(item[1], (int, float))):
            stack, weight = item
            weighted = True
        else:
            stack, weight = item, None
            weighted = False
        if not keep_for_thread_class(stack, thread_class):
            continue
        if smartmet_only:
            collapsed = collapse_to_smartmet(stack)
            if collapsed is None:
                continue
            stack = collapsed
        out.append((stack, weight) if weighted else stack)
    return out


# ---- pure helpers ----------------------------------------------------------

def _subtree_at(root: Dict[str, list],
                path: Tuple[str, ...]) -> Optional[Dict[str, list]]:
    """Walk `root` along `path` and return the children dict at the end.

    `root` is the {sym: [count, children]} structure produced by
    `_build_flame_tree`. Returns None if any segment of `path` is missing.
    """
    node = root
    for sym in path:
        entry = node.get(sym)
        if entry is None:
            return None
        node = entry[1]
    return node


def _sorted_children(children: Dict[str, list]) -> List[str]:
    """Symbols at one level, sorted by sample count desc."""
    return sorted(children.keys(), key=lambda s: -children[s][0])


# Frame layout returned by the renderer: one entry per drawn rectangle.
# (y, x_start, x_end, symbol, count, path-from-rendered-root)
FlameFrame = Tuple[int, int, int, str, int, Tuple[str, ...]]


def _render_flame(win, y_top: int, max_y: int, x_top: int, width: int,
                  root: Dict[str, list],
                  rendered_root_path: Tuple[str, ...],
                  highlight_path: Tuple[str, ...]) -> List[FlameFrame]:
    """Render the flame and return the frame map for cursor lookup.

    `rendered_root_path` is the absolute path of the visible subtree's
    root (used so the returned frames carry full paths back to the
    original tree). `highlight_path` is the full path of the frame to
    highlight; it's drawn with A_REVERSE | A_BOLD so the operator can
    see where the cursor is.
    """
    frames: List[FlameFrame] = []

    def recurse(y: int, x: int, w: int,
                children_dict: Dict[str, list],
                parent_count: int,
                path_so_far: Tuple[str, ...]) -> None:
        if y > max_y or w <= 0 or not children_dict:
            return
        total = parent_count if parent_count > 0 else 1
        items = sorted(children_dict.items(), key=lambda kv: -kv[1][0])
        cur_x = x
        remaining = w
        for sym, (cnt, kids) in items:
            if remaining <= 0:
                break
            cw = max(1, int(round(cnt / total * w)))
            cw = min(cw, remaining)
            if cw < 1:
                continue
            this_path = path_so_far + (sym,)
            frames.append((y, cur_x, cur_x + cw, sym, cnt, this_path))
            label = sym[:cw] if len(sym) > cw else sym + " " * (cw - len(sym))
            attr = _flame_color(sym)
            if this_path == highlight_path:
                attr = (theme.attr(theme.P_HIGHLIGHT,
                                   curses.A_BOLD | curses.A_UNDERLINE))
            safe_addstr(win, y, cur_x, label, attr)
            if kids and y < max_y:
                recurse(y + 1, cur_x, cw, kids, cnt, this_path)
            cur_x += cw
            remaining -= cw

    total_root = sum(v[0] for v in root.values()) or 1
    recurse(y_top, x_top, width, root, total_root, rendered_root_path)
    return frames


def _max_depth(frames: List[FlameFrame]) -> int:
    return max((f[0] for f in frames), default=0)


# ---- panel -----------------------------------------------------------------

class FlamePanel(Panel):
    name = "Flame"
    hotkey = "f"
    help_text = (
        "Live flamegraph for the focused smartmetd PID. "
        "↑↓←→ navigate, Enter zoom in, Esc/u zoom out, 0 reset zoom, "
        "n/N next/prev PID. Mode keys: C on-CPU, B off-CPU "
        "(blocked), L locks, M memory faults, W wakeup, I block-I/O, "
        "A allocations (dev-only, --malloc-flame). "
        "S toggles SmartMet-only filter (collapses to SmartMet frames "
        "+ ≤1 syscall leaf); T cycles thread class (all / request / "
        "background). Requires --perf."
    )
    panel_help = """\
A flamegraph stacks function calls vertically: the bottom row is the
program's entry point and each row above is a function called by the
row below. Frame WIDTH is what each mode measures — wider = more time
or more events in that path. Press the mode keys to switch what is
being measured:

C  on-CPU         where the CPU is going. Each sample is the call
                  stack at the moment of a 99 Hz timer tick.
                  Wide frames = functions actually running on a CPU.

B  off-CPU        where threads are BLOCKED. Each sample is one
                  context-switch out, weighted by µs blocked.
                  Wide frames = functions where threads spend most
                  of their NOT-running time. Read this when
                  on-CPU is healthy but request latency is up.

L  off-CPU (locks) the off-CPU view filtered to leaves that look
                  like lock waits (futex_*, pthread_mutex_*, etc).
                  Ranks the worst contention points by total wait
                  time. The natural follow-up to B.

M  page-faults    where in code smartmetd touches cold pages.
                  Each sample is one major fault — a synchronous
                  read from disk. Pairs with the page-fault
                  sparkline in the Proc panel.

W  wakeup         who is doing the unblocking. Dual of B/L:
                  off-CPU shows the lock-WAITER, wakeup shows the
                  lock-HOLDER. Walk between them to find a
                  contention pair.

I  block-I/O      where smartmetd issues block requests. Catches
                  every read/write/fsync, not just the ones routed
                  through page-cache misses. Pairs with the
                  Block I/O latency sparkline in the Proc panel.

A  allocations    (DEV-ONLY) bpftrace uprobe on malloc(); each
                  sample is one allocation, weighted by bytes.
                  Uses jemalloc / mimalloc / glibc auto-detected.
                  Off by default; pass --malloc-flame to enable.

Navigation: ↑↓←→ move the cursor frame. Enter zooms IN (the
selected frame becomes the new visible root). Esc / u zoom out one
level. 0 / Home reset to the full root. n / N switch the focused
smartmetd PID; backend processes are preferred over frontend by
default.

Filters (compose on top of the active mode):

S  smartmet-only   Collapse each stack to its SmartMet frames plus
                   at most one non-SmartMet leaf (the syscall / libc
                   the SmartMet code is calling into). Default ON
                   because the raw flame is dominated by libc and
                   kernel frames that crowd out the SmartMet code
                   the operator is investigating. Toggle off (S)
                   to see the full unfiltered stacks.

T  thread class    Cycle all → request → background. A stack is
                   "request" when it contains
                   SmartMetPlugin::callRequestHandler — i.e. the
                   thread was actively serving an HTTP request when
                   sampled. "background" is everything else
                   (cleanup, schedulers, cache eviction, etc).
                   Classification is by stack content rather than
                   thread name because spine does not pthread_setname_np;
                   every thread reports comm=smartmetd.

The bottom of the panel carries the "Top X functions" list —
re-skinned per mode (top symbols / blocked-on / contended locks /
fault-causing / wakeup-causing / I/O-issuing / allocation-causing).
"""

    def __init__(self) -> None:
        # Currently-zoomed root: empty tuple = whole tree.
        self.zoom_path: Tuple[str, ...] = ()
        # Currently-selected frame's full path. Empty = "no selection yet"
        # (set on first render when the frame map is populated).
        self.cursor_path: Tuple[str, ...] = ()
        # Last-rendered frame map — used by handle_key without re-running
        # the flame layout. The handler reads this to find siblings /
        # children / the parent of the current cursor frame.
        self._last_frames: List[FlameFrame] = []
        self._last_root: Dict[str, list] = {}
        # `s`-keyed record-duration selection overlay state.
        self._seconds_menu_open: bool = False
        self._seconds_menu_idx: int = 0
        # `a`-keyed analyse overlay state. The overlay lists findings
        # produced by sources.analyze.analyze() against the frozen
        # rings. Pause is sticky relative to the overlay: pressing `a`
        # toggles pause AND re-runs the analyser; Esc / Enter dismiss
        # the overlay but leave the recorders paused so the operator
        # can study the flame without it changing under them. Press
        # `a` again to resume recording.
        self._findings_overlay_open: bool = False
        self._findings: List[Finding] = []
        self._findings_idx: int = 0
        # `o` cycles through four modes:
        #   on-cpu          — perf record -F 99 -ag (default)
        #   off-cpu         — bcc-tools' offcputime, weighted by us-blocked
        #   off-cpu-locks   — same data filtered to lock-related leaves
        #                     (futex_*, pthread_mutex/cond/rwlock/spin)
        #   pagefault       — perf record -e major-faults, where in code
        #                     are the page-cache misses landing
        # State is per-panel so switching to another view and back
        # does not jump back to on-CPU unexpectedly.
        self.mode: str = "on-cpu"
        # SmartMet-only filter: collapse each stack to its SmartMet
        # frames plus at most one syscall / libc leaf. Default ON
        # because that's the view the operator wants by design — the
        # raw flame is dominated by libc / kernel frames that crowd
        # out the SmartMet code we're trying to see. Toggle with `S`.
        self.smartmet_only: bool = True
        # Thread-class filter (request / background / all). Classified
        # by stack content (presence of SmartMetPlugin::callRequestHandler)
        # rather than thread name — smartmetd does not pthread_setname_np,
        # so every thread shows comm=smartmetd and comm-based filtering
        # would be useless. Toggle with `T`.
        self.thread_class: str = THREAD_CLASS_ALL

    # ---- key handling ------------------------------------------------------

    def handle_key(self, key, store):
        # While the duration overlay is open, every keystroke goes there
        # — including arrows that would otherwise navigate the flame.
        if self._seconds_menu_open:
            return self._handle_seconds_menu_key(key, store)
        # Same precedence rule for the analyse overlay: while it's
        # open, arrows scroll the findings list and Enter picks one.
        if self._findings_overlay_open:
            return self._handle_findings_overlay_key(key, store)

        # PID switching is allowed regardless of perf state.
        procs = store.proc_list()
        pids = [p.pid for p in procs]
        if pids:
            selected = store.proc_selected()
            if selected is None or selected not in pids:
                # Use the role-aware default rather than the lowest
                # PID — keeps the focused process on a backend
                # whenever one exists, which is almost always what
                # the operator wants to profile.
                default = store.proc_default_pid() or pids[0]
                store.proc_select(default)
                selected = default
            if key == ord("n"):
                store.proc_select(pids[(pids.index(selected) + 1) % len(pids)])
                return True
            if key == ord("N"):
                store.proc_select(pids[(pids.index(selected) - 1) % len(pids)])
                return True
            # Direct selection by number — matches the [N] index shown
            # in the top-of-panel PID selector. Only digits 1-9 fit on
            # a row, so anything beyond the 9th PID needs n/N cycling.
            if ord("1") <= key <= ord("9"):
                idx = key - ord("1")
                if idx < len(pids):
                    store.proc_select(pids[idx])
                    return True

        # `s` opens the record-duration overlay even when there's no
        # data yet — the operator may want to reduce overhead before
        # the first cycle starts.
        if key == ord("s") and store.perf_enabled:
            self._open_seconds_menu(store)
            return True

        # `a` (analyse) toggles the freeze-and-analyse mode. First press
        # pauses every recorder (the rings stop receiving fresh stacks)
        # and runs the detector pack against the frozen ring. Second
        # press resumes recording and clears the overlay/findings.
        # Lowercase per the panel-hotkey case convention: uppercase
        # would conflict with the global panel switcher.
        if key == ord("a") and store.perf_enabled:
            if store.profile_paused:
                # Resume.
                store.profile_paused = False
                self._findings_overlay_open = False
                self._findings = []
                self._findings_idx = 0
            else:
                # Pause and analyse the focused PID. proc_selected()
                # is the same source the recorders read; no per-overlay
                # PID state needed.
                store.profile_paused = True
                pid = store.proc_selected()
                self._findings = analyze(store, pid) if pid is not None else []
                self._findings_idx = 0
                self._findings_overlay_open = True
            return True

        # Direct-key mode selection. Uppercase mnemonics so the
        # lowercase panel mnemonics (l=Logs, c=Caches, o=Overview, p=Proc)
        # still reach the global panel switcher when pressed from this
        # view — uppercase letters are not used by any panel mnemonic.
        # Resetting cursor and zoom on each change because each mode
        # has a completely different stack set; carrying the path
        # across modes lands the operator in a "no selection" state.
        mode_keys = {
            ord("C"): "on-cpu",         # CPU
            ord("B"): "off-cpu",        # Blocked
            ord("L"): "off-cpu-locks",  # Locks
            ord("M"): "pagefault",      # Memory faults
            ord("W"): "wakeup",         # Wakeup (who woke us)
            ord("I"): "blockflame",     # Block-I/O issue
            ord("A"): "malloc",         # Allocations (gated)
        }
        if key in mode_keys:
            new_mode = mode_keys[key]
            if new_mode != self.mode:
                self.mode = new_mode
                self.zoom_path = ()
                self.cursor_path = ()
                self._last_root = {}
                self._last_frames = []
            return True

        # `S` toggles SmartMet-only filtering. Frame symbols change
        # shape (frames disappear or new roots appear) so we drop the
        # zoom path and cursor — staying zoomed into a frame that just
        # vanished is confusing.
        if key == ord("S"):
            self.smartmet_only = not self.smartmet_only
            self.zoom_path = ()
            self.cursor_path = ()
            self._last_root = {}
            self._last_frames = []
            return True

        # `T` cycles the thread-class filter: all → request → background.
        # Symbols themselves don't change, only which stacks are kept,
        # so the existing zoom-walk-back logic in the draw paths handles
        # the case where the cursor's subtree disappears.
        if key == ord("T"):
            cycle = (THREAD_CLASS_ALL, THREAD_CLASS_REQUEST,
                     THREAD_CLASS_BACKGROUND)
            try:
                idx = cycle.index(self.thread_class)
            except ValueError:
                idx = -1
            self.thread_class = cycle[(idx + 1) % len(cycle)]
            return True

        # Flame navigation only meaningful when perf is on AND we have data.
        if not store.perf_enabled or not self._last_root:
            return False
        if key == curses.KEY_RIGHT:
            self._move_sibling(+1)
        elif key == curses.KEY_LEFT:
            self._move_sibling(-1)
        elif key == curses.KEY_DOWN:
            self._move_to_first_child()
        elif key == curses.KEY_UP:
            self._move_to_parent()
        elif key in (10, 13, curses.KEY_ENTER):
            self._zoom_in()
        elif key in (27, ord("u"), curses.KEY_BACKSPACE, 127, 8):
            self._zoom_out()
        elif key in (ord("0"), curses.KEY_HOME):
            self.zoom_path = ()
            self.cursor_path = ()
        else:
            return False
        return True

    # ---- analyse overlay --------------------------------------------------

    def _handle_findings_overlay_key(self, key, store) -> bool:
        if key == curses.KEY_UP:
            self._findings_idx = max(0, self._findings_idx - 1)
        elif key == curses.KEY_DOWN:
            self._findings_idx = min(max(0, len(self._findings) - 1),
                                     self._findings_idx + 1)
        elif key in (10, 13, curses.KEY_ENTER):
            # Jump the flame view to the selected finding's evidence.
            # Pause stays active so the flame doesn't drift while the
            # operator inspects the suspect path; press `a` to resume.
            if 0 <= self._findings_idx < len(self._findings):
                self._jump_to_finding(self._findings[self._findings_idx])
            self._findings_overlay_open = False
        elif key in (27, ord("q")):
            # Esc / q dismiss the overlay but leave pause active so the
            # operator can navigate the flame freely against the frozen
            # ring. Resume with another `a`.
            self._findings_overlay_open = False
        # Always intercept while open so stray keys don't leak through
        # to the flame navigation behind the modal.
        return True

    def _jump_to_finding(self, finding: Finding) -> None:
        """Switch flame mode to the finding's source ring and place
        the cursor on the evidence stack so the operator sees the
        suspect path framed and highlighted on next render."""
        if finding.mode != self.mode:
            self.mode = finding.mode
            self._last_root = {}
            self._last_frames = []
        # Reset zoom so the full evidence chain is visible from the
        # root — the operator can then zoom in by pressing Enter once
        # the cursor is parked on the suspect leaf.
        self.zoom_path = ()
        self.cursor_path = finding.evidence_stack

    # ---- record-duration overlay ------------------------------------------

    def _open_seconds_menu(self, store) -> None:
        self._seconds_menu_open = True
        current = getattr(store, "perf_record_seconds", 3)
        try:
            self._seconds_menu_idx = PERF_SECONDS_PRESETS.index(current)
        except ValueError:
            # Out-of-band value (e.g. user passed --perf-record-seconds 7).
            # Land on the closest preset so arrow keys feel sensible.
            self._seconds_menu_idx = min(
                range(len(PERF_SECONDS_PRESETS)),
                key=lambda i: abs(PERF_SECONDS_PRESETS[i] - current),
            )

    def _handle_seconds_menu_key(self, key, store) -> bool:
        if key == curses.KEY_UP:
            self._seconds_menu_idx = max(0, self._seconds_menu_idx - 1)
        elif key == curses.KEY_DOWN:
            self._seconds_menu_idx = min(len(PERF_SECONDS_PRESETS) - 1,
                                          self._seconds_menu_idx + 1)
        elif key in (10, 13, curses.KEY_ENTER):
            store.perf_record_seconds = PERF_SECONDS_PRESETS[self._seconds_menu_idx]
            self._seconds_menu_open = False
        elif key in (27, ord("s"), ord("q")):
            self._seconds_menu_open = False
        # Always intercept while the overlay is open so stray keys
        # don't navigate the flame behind it.
        return True

    # ---- navigation primitives ---------------------------------------------

    def _ensure_cursor(self) -> None:
        """Initialise the cursor at the visible root if it's empty or the
        previous selection no longer exists in the rendered frame map."""
        if not self._last_frames:
            return
        valid_paths = {f[5] for f in self._last_frames}
        if self.cursor_path in valid_paths:
            return
        # Fall back to the visible root (the first frame in the map).
        self.cursor_path = self._last_frames[0][5]

    def _frame_at(self, path: Tuple[str, ...]) -> Optional[FlameFrame]:
        for f in self._last_frames:
            if f[5] == path:
                return f
        return None

    def _move_sibling(self, delta: int) -> None:
        self._ensure_cursor()
        if not self.cursor_path:
            return
        parent = self.cursor_path[:-1]
        siblings = sorted(
            (f for f in self._last_frames
             if f[5][:-1] == parent and len(f[5]) == len(self.cursor_path)),
            key=lambda f: f[1],
        )
        try:
            idx = next(i for i, f in enumerate(siblings)
                       if f[5] == self.cursor_path)
        except StopIteration:
            return
        new = idx + delta
        if 0 <= new < len(siblings):
            self.cursor_path = siblings[new][5]

    def _move_to_first_child(self) -> None:
        self._ensure_cursor()
        if not self.cursor_path:
            return
        children = [f for f in self._last_frames
                    if f[5][:-1] == self.cursor_path
                    and len(f[5]) == len(self.cursor_path) + 1]
        if not children:
            return
        # Most-used child = highest count (drawn leftmost in our layout).
        children.sort(key=lambda f: -f[4])
        self.cursor_path = children[0][5]

    def _move_to_parent(self) -> None:
        self._ensure_cursor()
        if len(self.cursor_path) <= len(self.zoom_path):
            return
        self.cursor_path = self.cursor_path[:-1]

    def _zoom_in(self) -> None:
        self._ensure_cursor()
        if not self.cursor_path:
            return
        # Selected frame's full absolute path becomes the new visible root.
        self.zoom_path = self.cursor_path
        # Cursor stays at the new root (shown as the topmost frame).

    def _empty_after_filter_msg(self) -> str:
        """Diagnostic shown when the SmartMet-only / thread-class filter
        leaves zero stacks. Names the active filter so the operator
        knows which key to press to widen it."""
        bits = []
        if self.thread_class != THREAD_CLASS_ALL:
            bits.append(f"thread={self.thread_class}")
        if self.smartmet_only:
            bits.append("SmartMet-only")
        if not bits:
            return "no samples in the retained ring"
        return (f"no stacks match active filter ({', '.join(bits)}); "
                f"press T or S to widen")

    def _zoom_out(self) -> None:
        if not self.zoom_path:
            return
        self.zoom_path = self.zoom_path[:-1]
        # Pull the cursor up so it stays inside the visible tree.
        if len(self.cursor_path) < len(self.zoom_path):
            self.cursor_path = self.zoom_path

    # ---- export ------------------------------------------------------------

    def export_snapshot(self, store):
        # Hierarchical — flat CSV would lose the structure. The
        # FlameSnapshot exists for the web view (which can serve folded
        # stacks as JSON), but the curses CSV path declines to export.
        return None, None

    # ---- drawing -----------------------------------------------------------

    def draw(self, win, store):
        h, w = win.getmaxyx()
        if not store.perf_enabled:
            self._draw_disabled(win, store)
            return
        procs = store.proc_list()
        if not procs:
            safe_addstr(win, 0, 0,
                        " Flame — no smartmetd processes found".ljust(w - 1),
                        theme.attr(theme.P_TAB_ACTIVE))
            return
        selected = store.proc_selected()
        if selected is None or selected not in [p.pid for p in procs]:
            selected = store.proc_default_pid() or procs[0].pid
            store.proc_select(selected)
        info = next((p for p in procs if p.pid == selected), procs[0])

        # PID selector at the very top so the operator can see all
        # smartmetd PIDs and switch between them with a number key.
        sel_bottom = draw_pid_selector(win, store, top=0)
        self._draw_header(win, info, store, len(procs), sel_bottom)
        flame_bottom = self._draw_flame_section(win, store, info, sel_bottom + 1)
        self._draw_top_symbols(win, store, info, flame_bottom)
        self._draw_footer(win, n_procs=len(procs))
        # Draw the modal overlays last so they sit on top of the flame.
        # Only one is open at a time (handle_key gates entry on the
        # other being closed), so the order between these two doesn't
        # matter — but the seconds menu is small and the findings
        # overlay can be tall, so we draw findings first.
        if self._findings_overlay_open:
            self._draw_findings_overlay(win, store)
        if self._seconds_menu_open:
            self._draw_seconds_menu(win, store)

    # ---- rendering pieces --------------------------------------------------

    def _draw_disabled(self, win, store) -> None:
        h, w = win.getmaxyx()
        safe_addstr(win, 0, 0,
                    f" Flame — {store.perf_status}".ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))
        safe_addstr(win, 2, 2,
                    "The flamegraph view needs the perf sampler running. "
                    "Common causes:",
                    theme.attr(theme.P_DIM))
        safe_addstr(win, 3, 4,
                    "* smtop wasn't launched with --perf",
                    theme.attr(theme.P_DIM))
        safe_addstr(win, 4, 4,
                    "* `perf` (linux-tools) is not installed on this host",
                    theme.attr(theme.P_DIM))
        safe_addstr(win, 5, 4,
                    "* kernel.perf_event_paranoid > 2 and smtop wasn't run as root",
                    theme.attr(theme.P_DIM))

    def _draw_header(self, win, info: ProcInfo, store, n_procs: int,
                     row: int) -> None:
        h, w = win.getmaxyx()
        breadcrumb = " > ".join(self.zoom_path) if self.zoom_path else "(root)"
        # Compact filter badge — present in every mode's header so the
        # operator can always see whether they're looking at the
        # SmartMet-only / thread-filtered view or the raw stacks.
        filt = (f"smartmet-only={'on' if self.smartmet_only else 'off'} "
                f"thread={self.thread_class}")
        if self.mode == "off-cpu":
            total_us = store.offcpu_last_total_us(info.pid)
            ms = total_us // 1000
            header = (
                f" Flame off-CPU — pid={info.pid}  "
                f"status={store.offcpu_status}  "
                f"last_off={ms} ms  zoom={breadcrumb}  {filt}"
            )
        elif self.mode == "off-cpu-locks":
            total_us = store.offcpu_last_total_us(info.pid)
            ms = total_us // 1000
            header = (
                f" Flame off-CPU (locks only) — pid={info.pid}  "
                f"status={store.offcpu_status}  "
                f"last_off={ms} ms  zoom={breadcrumb}  {filt}"
            )
        elif self.mode == "pagefault":
            sample_count = store.pagefault_last_sample_count(info.pid)
            header = (
                f" Flame page-faults — pid={info.pid}  "
                f"status={store.pagefault_status}  "
                f"last={sample_count} faults  zoom={breadcrumb}  {filt}"
            )
        elif self.mode == "wakeup":
            sample_count = store.wakeup_last_sample_count(info.pid)
            header = (
                f" Flame wakeup — pid={info.pid}  "
                f"status={store.wakeup_status}  "
                f"last={sample_count} wakeups  zoom={breadcrumb}  {filt}"
            )
        elif self.mode == "blockflame":
            sample_count = store.blockflame_last_sample_count(info.pid)
            header = (
                f" Flame block-I/O issue — pid={info.pid}  "
                f"status={store.blockflame_status}  "
                f"last={sample_count} requests  zoom={breadcrumb}  {filt}"
            )
        elif self.mode == "malloc":
            total_bytes = store.malloc_last_total_bytes(info.pid)
            header = (
                f" Flame malloc — pid={info.pid}  "
                f"status={store.malloc_status}  "
                f"last={total_bytes} bytes  alloc={store.malloc_allocator}  "
                f"zoom={breadcrumb}"
            )
        else:
            sample_count = store.perf_last_sample_count(info.pid)
            header = (
                f" Flame on-CPU — pid={info.pid}  status={store.perf_status}  "
                f"last={sample_count}  zoom={breadcrumb}  {filt}"
            )
        # Stamp a PAUSED marker on the header line whenever the
        # analyse-mode freeze switch is engaged. The recorders are
        # idle and the rings are frozen — the operator needs to see
        # this at a glance so they don't mistake the unchanging
        # flame for a stuck sampler.
        if getattr(store, "profile_paused", False):
            header = header + "  [PAUSED — press a to resume]"
        safe_addstr(win, row, 0, header.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

    def _draw_flame_section(self, win, store, info: ProcInfo,
                            top: int) -> int:
        """Render the flame tree starting at row `top`. Returns the
        bottom row index it used so the top-symbols list knows where
        to start."""
        h, w = win.getmaxyx()
        if self.mode in ("off-cpu", "off-cpu-locks"):
            return self._draw_off_cpu_flame(win, store, info, top)
        if self.mode == "pagefault":
            return self._draw_pagefault_flame(win, store, info, top)
        if self.mode == "wakeup":
            return self._draw_perf_event_flame(
                win, store, info, top,
                store.wakeup_recent_stacks(info.pid),
                store.wakeup_enabled,
                store.wakeup_last_error,
                "wakeup samples", "perf",
            )
        if self.mode == "blockflame":
            return self._draw_perf_event_flame(
                win, store, info, top,
                store.blockflame_recent_stacks(info.pid),
                store.blockflame_enabled,
                store.blockflame_last_error,
                "block-I/O issue samples", "perf",
            )
        if self.mode == "malloc":
            return self._draw_malloc_flame(win, store, info, top)
        # If a previous cycle failed, surface that in place of the flame.
        if store.perf_last_error:
            self._draw_perf_error(win, top, h - 2, store.perf_last_error)
            self._last_frames = []
            self._last_root = {}
            return h - 2
        stacks = store.perf_recent_stacks(info.pid)
        if not stacks:
            safe_addstr(win, top, 2,
                        "no stack samples yet — waiting for first perf cycle…",
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return top + 1
        stacks = _apply_filters(stacks, self.thread_class,
                                self.smartmet_only)
        if not stacks:
            safe_addstr(win, top, 2,
                        self._empty_after_filter_msg(),
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return top + 1
        # Build the tree from the entire retained stack ring (the store
        # bounds it at 20000) so each rebuild reflects roughly the last
        # ~25-60 seconds of sampling. Slicing was a leftover from when
        # the ring was new and we were paranoid about CPU cost; tree
        # construction is fast.
        full_root = _build_flame_tree(stacks)
        self._last_root = full_root
        if self.zoom_path:
            visible_root = _subtree_at(full_root, self.zoom_path)
            if visible_root is None or not visible_root:
                # Zoomed function disappeared from the latest stacks —
                # quietly walk back up until we find something to render.
                while self.zoom_path:
                    self.zoom_path = self.zoom_path[:-1]
                    visible_root = (_subtree_at(full_root, self.zoom_path)
                                    if self.zoom_path else full_root)
                    if visible_root:
                        break
                if not visible_root:
                    visible_root = full_root
        else:
            visible_root = full_root

        # Cap flame to half the screen so the symbol list always has room.
        flame_top = top
        flame_max_y = max(flame_top + 2, h - 12)
        self._ensure_cursor()
        frames = _render_flame(
            win, flame_top, flame_max_y, 0, w - 1,
            visible_root, self.zoom_path, self.cursor_path,
        )
        self._last_frames = frames
        # If the cursor wasn't valid (first render or after PID switch),
        # try again now that we know the new frame map.
        if not self._frame_at(self.cursor_path):
            self._ensure_cursor()
            # Rerender just the highlight without rebuilding everything:
            target = self._frame_at(self.cursor_path)
            if target is not None:
                y, xs, xe, sym, _cnt, _path = target
                cw = xe - xs
                label = sym[:cw] if len(sym) > cw else sym + " " * (cw - len(sym))
                safe_addstr(win, y, xs, label,
                            theme.attr(theme.P_HIGHLIGHT,
                                       curses.A_BOLD | curses.A_UNDERLINE))
        bottom = _max_depth(frames) if frames else flame_top
        return bottom + 1

    def _draw_off_cpu_flame(self, win, store, info: ProcInfo,
                            top: int) -> int:
        """Off-CPU flame: stacks weighted by microseconds blocked.

        Same renderer as the on-CPU path; the only differences are
        which ring we pull from, what error we surface on failure,
        and what message we show when there is no data yet.
        """
        h, w = win.getmaxyx()
        if not store.offcpu_enabled:
            self._draw_offcpu_unavailable(win, store, top)
            self._last_frames = []
            self._last_root = {}
            return min(top + 6, h - 2)
        if store.offcpu_last_error:
            self._draw_perf_error(win, top, h - 2, store.offcpu_last_error)
            self._last_frames = []
            self._last_root = {}
            return h - 2
        weighted = store.offcpu_recent_stacks(info.pid)
        if self.mode == "off-cpu-locks":
            # Filter to stacks whose leaf is a known lock-wait symbol.
            # Keeps the off-CPU recorder's wait-time weighting intact
            # so the flame still measures milliseconds-blocked, but
            # restricted to mutex / futex / cond / rwlock / spin
            # entries — answers "where am I sleeping on a lock?"
            # specifically.
            weighted = [(s, w) for s, w in weighted if _is_lock_stack(s)]
        weighted = _apply_filters(weighted, self.thread_class,
                                  self.smartmet_only)
        if not weighted:
            empty_msg = ("no off-CPU samples yet — waiting for first cycle…"
                         if self.mode == "off-cpu"
                         else "no lock-wait stacks in the retained ring")
            safe_addstr(win, top, 2, empty_msg, theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return top + 1
        full_root = _build_flame_tree(weighted)
        self._last_root = full_root
        if self.zoom_path:
            visible_root = _subtree_at(full_root, self.zoom_path)
            if visible_root is None or not visible_root:
                while self.zoom_path:
                    self.zoom_path = self.zoom_path[:-1]
                    visible_root = (_subtree_at(full_root, self.zoom_path)
                                    if self.zoom_path else full_root)
                    if visible_root:
                        break
                if not visible_root:
                    visible_root = full_root
        else:
            visible_root = full_root
        flame_top = top
        flame_max_y = max(flame_top + 2, h - 12)
        self._ensure_cursor()
        frames = _render_flame(
            win, flame_top, flame_max_y, 0, w - 1,
            visible_root, self.zoom_path, self.cursor_path,
        )
        self._last_frames = frames
        if not self._frame_at(self.cursor_path):
            self._ensure_cursor()
            target = self._frame_at(self.cursor_path)
            if target is not None:
                y, xs, xe, sym, _cnt, _path = target
                cw = xe - xs
                label = sym[:cw] if len(sym) > cw else sym + " " * (cw - len(sym))
                safe_addstr(win, y, xs, label,
                            theme.attr(theme.P_HIGHLIGHT,
                                       curses.A_BOLD | curses.A_UNDERLINE))
        bottom = _max_depth(frames) if frames else flame_top
        return bottom + 1

    def _draw_pagefault_flame(self, win, store, info: ProcInfo,
                              top: int) -> int:
        """Page-fault flame: where in code does smartmetd touch cold pages?

        Each sample is one major fault — a synchronous read from disk
        — so the flame width measures fault count per stack. The most
        useful pairing is with the per-PID page-fault sparkline in the
        Proc panel: when that sparkline spikes, this flame names the
        function that caused the spike.
        """
        h, w = win.getmaxyx()
        if not store.pagefault_enabled:
            safe_addstr(win, top, 2,
                        "page-fault flame needs --perf",
                        theme.attr(theme.P_BAD, curses.A_BOLD))
            safe_addstr(win, top + 1, 2,
                        "Same perf access as the on-CPU sampler. "
                        "Press 'o' to switch back.",
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return min(top + 4, h - 2)
        if store.pagefault_last_error:
            self._draw_perf_error(win, top, h - 2,
                                  store.pagefault_last_error)
            self._last_frames = []
            self._last_root = {}
            return h - 2
        stacks = store.pagefault_recent_stacks(info.pid)
        if not stacks:
            safe_addstr(win, top, 2,
                        "no page-fault samples yet — waiting for first "
                        "fault on this PID…",
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return top + 1
        stacks = _apply_filters(stacks, self.thread_class,
                                self.smartmet_only)
        if not stacks:
            safe_addstr(win, top, 2,
                        self._empty_after_filter_msg(),
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return top + 1
        full_root = _build_flame_tree(stacks)
        self._last_root = full_root
        if self.zoom_path:
            visible_root = _subtree_at(full_root, self.zoom_path)
            if visible_root is None or not visible_root:
                while self.zoom_path:
                    self.zoom_path = self.zoom_path[:-1]
                    visible_root = (_subtree_at(full_root, self.zoom_path)
                                    if self.zoom_path else full_root)
                    if visible_root:
                        break
                if not visible_root:
                    visible_root = full_root
        else:
            visible_root = full_root
        flame_top = top
        flame_max_y = max(flame_top + 2, h - 12)
        self._ensure_cursor()
        frames = _render_flame(
            win, flame_top, flame_max_y, 0, w - 1,
            visible_root, self.zoom_path, self.cursor_path,
        )
        self._last_frames = frames
        if not self._frame_at(self.cursor_path):
            self._ensure_cursor()
            target = self._frame_at(self.cursor_path)
            if target is not None:
                y, xs, xe, sym, _cnt, _path = target
                cw = xe - xs
                label = sym[:cw] if len(sym) > cw else sym + " " * (cw - len(sym))
                safe_addstr(win, y, xs, label,
                            theme.attr(theme.P_HIGHLIGHT,
                                       curses.A_BOLD | curses.A_UNDERLINE))
        bottom = _max_depth(frames) if frames else flame_top
        return bottom + 1

    def _draw_perf_event_flame(self, win, store, info: ProcInfo,
                               top: int, stacks, enabled: bool,
                               last_error: str,
                               sample_label: str, backend: str) -> int:
        """Generic perf-event flame (wakeup, block-I/O issue, etc).

        Same renderer as on-CPU and pagefault — different stack source,
        different empty / disabled / error messages.
        """
        h, w = win.getmaxyx()
        if not enabled:
            safe_addstr(win, top, 2,
                        f"this flame mode needs --perf ({backend})",
                        theme.attr(theme.P_BAD, curses.A_BOLD))
            self._last_frames = []
            self._last_root = {}
            return min(top + 4, h - 2)
        if last_error:
            self._draw_perf_error(win, top, h - 2, last_error)
            self._last_frames = []
            self._last_root = {}
            return h - 2
        if not stacks:
            safe_addstr(win, top, 2,
                        f"no {sample_label} yet — waiting for first cycle…",
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return top + 1
        stacks = _apply_filters(stacks, self.thread_class,
                                self.smartmet_only)
        if not stacks:
            safe_addstr(win, top, 2,
                        self._empty_after_filter_msg(),
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return top + 1
        full_root = _build_flame_tree(stacks)
        return self._render_with_zoom(win, full_root, top)

    def _draw_malloc_flame(self, win, store, info: ProcInfo,
                           top: int) -> int:
        """Allocation flame: stacks weighted by total bytes allocated.

        Gated on the operator passing --malloc-flame at startup. When
        the recorder isn't running we surface an explicit warning so
        a junior dev cannot accidentally enable it on production by
        pressing 'A' while connected to the wrong host.
        """
        h, w = win.getmaxyx()
        if not store.malloc_enabled:
            safe_addstr(win, top, 2,
                        "Malloc flamegraph is OFF",
                        theme.attr(theme.P_BAD, curses.A_BOLD))
            safe_addstr(win, top + 1, 2,
                        "Status: " + store.malloc_status,
                        theme.attr(theme.P_DIM))
            safe_addstr(win, top + 3, 2,
                        "WARNING: do NOT enable on production servers.",
                        theme.attr(theme.P_BAD, curses.A_BOLD))
            safe_addstr(win, top + 4, 2,
                        "The recorder uses bpftrace uprobes on every "
                        "malloc() in",
                        theme.attr(theme.P_DIM))
            safe_addstr(win, top + 5, 2,
                        "smartmetd; the per-call kernel breakpoint can add "
                        "measurable",
                        theme.attr(theme.P_DIM))
            safe_addstr(win, top + 6, 2,
                        "latency to every alloc and slow request handling "
                        "visibly.",
                        theme.attr(theme.P_DIM))
            safe_addstr(win, top + 8, 2,
                        "On a dev / staging host, restart smtop with:",
                        theme.attr(theme.P_DIM))
            safe_addstr(win, top + 9, 4,
                        "smtop --perf --malloc-flame …",
                        theme.attr(theme.P_HEADER))
            safe_addstr(win, top + 10, 2,
                        "Default min-bytes = 4096; raise to filter more, "
                        "or pass 0",
                        theme.attr(theme.P_DIM))
            safe_addstr(win, top + 11, 2,
                        "to trace every allocation (extreme overhead).",
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return min(top + 13, h - 2)
        if store.malloc_last_error:
            self._draw_perf_error(win, top, h - 2, store.malloc_last_error)
            self._last_frames = []
            self._last_root = {}
            return h - 2
        weighted = store.malloc_recent_stacks(info.pid)
        if not weighted:
            safe_addstr(win, top, 2,
                        "no allocation samples yet — waiting for first "
                        "bpftrace cycle…",
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return top + 1
        weighted = _apply_filters(weighted, self.thread_class,
                                  self.smartmet_only)
        if not weighted:
            safe_addstr(win, top, 2,
                        self._empty_after_filter_msg(),
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return top + 1
        full_root = _build_flame_tree(weighted)
        return self._render_with_zoom(win, full_root, top)

    def _render_with_zoom(self, win, full_root, top: int) -> int:
        """Shared zoom + render path used by all flame modes that
        carry a fully-built tree at this point."""
        h, w = win.getmaxyx()
        self._last_root = full_root
        if self.zoom_path:
            visible_root = _subtree_at(full_root, self.zoom_path)
            if visible_root is None or not visible_root:
                while self.zoom_path:
                    self.zoom_path = self.zoom_path[:-1]
                    visible_root = (_subtree_at(full_root, self.zoom_path)
                                    if self.zoom_path else full_root)
                    if visible_root:
                        break
                if not visible_root:
                    visible_root = full_root
        else:
            visible_root = full_root
        flame_top = top
        flame_max_y = max(flame_top + 2, h - 12)
        self._ensure_cursor()
        frames = _render_flame(
            win, flame_top, flame_max_y, 0, w - 1,
            visible_root, self.zoom_path, self.cursor_path,
        )
        self._last_frames = frames
        if not self._frame_at(self.cursor_path):
            self._ensure_cursor()
            target = self._frame_at(self.cursor_path)
            if target is not None:
                y, xs, xe, sym, _cnt, _path = target
                cw = xe - xs
                label = sym[:cw] if len(sym) > cw else sym + " " * (cw - len(sym))
                safe_addstr(win, y, xs, label,
                            theme.attr(theme.P_HIGHLIGHT,
                                       curses.A_BOLD | curses.A_UNDERLINE))
        bottom = _max_depth(frames) if frames else flame_top
        return bottom + 1

    def _draw_offcpu_unavailable(self, win, store, top: int) -> None:
        """Surface the install hint inline when no off-CPU backend is
        present. The hint comes from profile_caps.offcpu_backend()
        and lands in store.offcpu_status."""
        h, w = win.getmaxyx()
        safe_addstr(win, top, 2,
                    "off-CPU profiling unavailable on this host",
                    theme.attr(theme.P_BAD, curses.A_BOLD))
        safe_addstr(win, top + 1, 4, store.offcpu_status[:max(0, w - 6)],
                    theme.attr(theme.P_BAD))
        safe_addstr(win, top + 3, 2,
                    "Install bcc-tools for the preferred eBPF backend, e.g.:",
                    theme.attr(theme.P_DIM))
        safe_addstr(win, top + 4, 4,
                    "sudo dnf install bcc-tools",
                    theme.attr(theme.P_DIM))
        safe_addstr(win, top + 5, 2,
                    "Then restart smtop. Press 'o' to switch back to on-CPU.",
                    theme.attr(theme.P_DIM))

    def _draw_top_symbols(self, win, store, info: ProcInfo,
                          start_row: int) -> None:
        h, w = win.getmaxyx()
        if start_row >= h - 2:
            return
        if self.mode in ("off-cpu", "off-cpu-locks"):
            self._draw_top_off_cpu_leaves(win, store, info, start_row,
                                          locks_only=(self.mode == "off-cpu-locks"))
            return
        if self.mode == "pagefault":
            self._draw_top_pagefault_leaves(win, store, info, start_row)
            return
        if self.mode == "wakeup":
            self._draw_top_count_leaves(
                win, info, start_row,
                store.wakeup_recent_stacks(info.pid),
                "Top wakeup-causing functions", "wakeups",
            )
            return
        if self.mode == "blockflame":
            self._draw_top_count_leaves(
                win, info, start_row,
                store.blockflame_recent_stacks(info.pid),
                "Top block-I/O issuing functions", "I/Os",
            )
            return
        if self.mode == "malloc":
            self._draw_top_byte_leaves(
                win, info, start_row,
                store.malloc_recent_stacks(info.pid),
            )
            return
        # Divider
        safe_addstr(win, start_row, 0,
                    "─ Top symbols (last 10 min) "
                    + "─" * max(0, w - 28),
                    theme.attr(theme.P_DIM))
        body_top = start_row + 1
        avail = h - body_top - 1  # leave footer row
        if avail < 1:
            return
        rows = store.perf_top_symbols(info.pid, minutes=10, n=avail)
        if not rows:
            safe_addstr(win, body_top, 2,
                        "no aggregated samples yet",
                        theme.attr(theme.P_DIM))
            return
        total = sum(c for _, c in rows) or 1
        spark_w = max(20, min(60, w - 60))
        for i, (sym, cnt) in enumerate(rows):
            y = body_top + i
            if y >= h - 1:
                break
            pct = cnt / total * 100
            series = store.perf_symbol_series(info.pid, sym, minutes=20)
            label = sym[:max(0, w - spark_w - 18)]
            cells = [
                (f"  {pct:>5.1f}%  ", theme.attr(theme.P_HEADER)),
                (f"{label:<{max(20, w - spark_w - 16)}}  ", 0),
            ]
            x = write_row(win, y, 0, cells)
            if series:
                safe_addstr(win, y, x, sparkline(series, width=spark_w),
                            theme.attr(theme.P_SPARK))

    def _draw_top_off_cpu_leaves(self, win, store, info: ProcInfo,
                                 start_row: int,
                                 locks_only: bool = False) -> None:
        """Top blocked-on functions = leaf-symbol weights summed across
        the off-CPU stack ring. With locks_only=True the input is
        filtered to lock-related leaves so the list ranks the
        contention points by total wait time. Per-minute history isn't
        tracked for off-CPU (not yet) so the row carries time-blocked
        instead of a sparkline."""
        h, w = win.getmaxyx()
        title = ("Top contended locks (retained ring)" if locks_only
                 else "Top blocked-on functions (off-CPU, retained ring)")
        safe_addstr(win, start_row, 0,
                    f"─ {title} "
                    + "─" * max(0, w - len(title) - 3),
                    theme.attr(theme.P_DIM))
        body_top = start_row + 1
        avail = h - body_top - 1
        if avail < 1:
            return
        weighted = store.offcpu_recent_stacks(info.pid)
        if locks_only:
            weighted = [(s, us) for s, us in weighted if _is_lock_stack(s)]
        if not weighted:
            msg = ("no lock-wait stacks in the retained ring"
                   if locks_only else "no off-CPU samples yet")
            safe_addstr(win, body_top, 2, msg, theme.attr(theme.P_DIM))
            return
        # Aggregate microseconds-blocked per leaf symbol.
        leaf_us: Dict[str, int] = {}
        total_us = 0
        for stack, us in weighted:
            if not stack:
                continue
            leaf = stack[-1]
            leaf_us[leaf] = leaf_us.get(leaf, 0) + us
            total_us += us
        if total_us <= 0:
            return
        rows = sorted(leaf_us.items(), key=lambda kv: -kv[1])[:avail]
        for i, (sym, us) in enumerate(rows):
            y = body_top + i
            if y >= h - 1:
                break
            pct = us / total_us * 100
            ms = us / 1000.0
            label = sym[:max(0, w - 30)]
            cells = [
                (f"  {pct:>5.1f}%  ", theme.attr(theme.P_HEADER)),
                (f"{ms:>10.1f} ms  ", theme.attr(theme.P_ACCENT)),
                (label, 0),
            ]
            write_row(win, y, 0, cells)

    def _draw_top_count_leaves(self, win, info: ProcInfo,
                               start_row: int, stacks,
                               title: str, unit: str) -> None:
        """Generic 'top leaf-symbol counts' summary used by the wakeup
        and block-I/O flame modes. Each input stack contributes 1 to
        its leaf — same shape as the on-CPU summary."""
        h, w = win.getmaxyx()
        safe_addstr(win, start_row, 0,
                    f"─ {title} "
                    + "─" * max(0, w - len(title) - 3),
                    theme.attr(theme.P_DIM))
        body_top = start_row + 1
        avail = h - body_top - 1
        if avail < 1:
            return
        if not stacks:
            safe_addstr(win, body_top, 2, "no samples yet",
                        theme.attr(theme.P_DIM))
            return
        leaf_count: Dict[str, int] = {}
        total = 0
        for stack in stacks:
            if not stack:
                continue
            leaf = stack[-1]
            leaf_count[leaf] = leaf_count.get(leaf, 0) + 1
            total += 1
        if total <= 0:
            return
        rows = sorted(leaf_count.items(), key=lambda kv: -kv[1])[:avail]
        for i, (sym, n) in enumerate(rows):
            y = body_top + i
            if y >= h - 1:
                break
            pct = n / total * 100
            label = sym[:max(0, w - 26)]
            cells = [
                (f"  {pct:>5.1f}%  ", theme.attr(theme.P_HEADER)),
                (f"{n:>6} {unit:<8}  ", theme.attr(theme.P_ACCENT)),
                (label, 0),
            ]
            write_row(win, y, 0, cells)

    def _draw_top_byte_leaves(self, win, info: ProcInfo,
                              start_row: int,
                              stacks_with_bytes) -> None:
        """Allocation-flame leaf summary: bytes allocated per leaf."""
        h, w = win.getmaxyx()
        title = "Top allocation-causing functions (malloc flame)"
        safe_addstr(win, start_row, 0,
                    f"─ {title} "
                    + "─" * max(0, w - len(title) - 3),
                    theme.attr(theme.P_DIM))
        body_top = start_row + 1
        avail = h - body_top - 1
        if avail < 1:
            return
        if not stacks_with_bytes:
            safe_addstr(win, body_top, 2, "no allocations recorded yet",
                        theme.attr(theme.P_DIM))
            return
        leaf_bytes: Dict[str, int] = {}
        total = 0
        for stack, n in stacks_with_bytes:
            if not stack or n <= 0:
                continue
            leaf = stack[-1]
            leaf_bytes[leaf] = leaf_bytes.get(leaf, 0) + n
            total += n
        if total <= 0:
            return
        rows = sorted(leaf_bytes.items(), key=lambda kv: -kv[1])[:avail]
        for i, (sym, n) in enumerate(rows):
            y = body_top + i
            if y >= h - 1:
                break
            pct = n / total * 100
            label = sym[:max(0, w - 32)]
            from ..widgets.bars import human_bytes
            cells = [
                (f"  {pct:>5.1f}%  ", theme.attr(theme.P_HEADER)),
                (f"{human_bytes(n):>10}  ", theme.attr(theme.P_ACCENT)),
                (label, 0),
            ]
            write_row(win, y, 0, cells)

    def _draw_top_pagefault_leaves(self, win, store, info: ProcInfo,
                                   start_row: int) -> None:
        """Top fault-causing functions: leaf-symbol counts summed across
        the page-fault stack ring. Each sample is one major fault, so
        the percentages express the fraction of recent faults each
        function caused."""
        h, w = win.getmaxyx()
        safe_addstr(win, start_row, 0,
                    "─ Top fault-causing functions (page-fault flame) "
                    + "─" * max(0, w - 51),
                    theme.attr(theme.P_DIM))
        body_top = start_row + 1
        avail = h - body_top - 1
        if avail < 1:
            return
        stacks = store.pagefault_recent_stacks(info.pid)
        if not stacks:
            safe_addstr(win, body_top, 2,
                        "no page-fault samples yet",
                        theme.attr(theme.P_DIM))
            return
        leaf_count: Dict[str, int] = {}
        total = 0
        for stack in stacks:
            if not stack:
                continue
            leaf = stack[-1]
            leaf_count[leaf] = leaf_count.get(leaf, 0) + 1
            total += 1
        if total <= 0:
            return
        rows = sorted(leaf_count.items(), key=lambda kv: -kv[1])[:avail]
        for i, (sym, n) in enumerate(rows):
            y = body_top + i
            if y >= h - 1:
                break
            pct = n / total * 100
            label = sym[:max(0, w - 26)]
            cells = [
                (f"  {pct:>5.1f}%  ", theme.attr(theme.P_HEADER)),
                (f"{n:>6} faults  ", theme.attr(theme.P_ACCENT)),
                (label, 0),
            ]
            write_row(win, y, 0, cells)

    def _draw_perf_error(self, win, top: int, max_y: int, msg: str) -> None:
        h, w = win.getmaxyx()
        safe_addstr(win, top, 2,
                    "perf cycle failed — showing the diagnostic in full:",
                    theme.attr(theme.P_BAD, curses.A_BOLD))
        for i, line in enumerate(msg.splitlines() or [msg], start=1):
            if top + i >= max_y:
                break
            safe_addstr(win, top + i, 4, line[:max(0, w - 6)],
                        theme.attr(theme.P_BAD))

    def _draw_footer(self, win, n_procs: int) -> None:
        h, w = win.getmaxyx()
        if h < 2:
            return
        hot = theme.attr(theme.P_MNEMONIC, curses.A_BOLD | curses.A_UNDERLINE)
        base = theme.attr(theme.P_TITLE)
        x = 0
        safe_addstr(win, h - 1, 0, " ", base); x += 1
        x = write_label(win, h - 1, x, "↑↓←→", 0, base, base)
        x = write_label(win, h - 1, x, " navigate  ", 0, base, base)
        x = write_label(win, h - 1, x, "Enter", 0, base, base)
        x = write_label(win, h - 1, x, " zoom in  ", 0, base, base)
        x = write_label(win, h - 1, x, "Esc", 0, base, base)
        x = write_label(win, h - 1, x, "/", 0, base, base)
        x = write_label(win, h - 1, x, "u", 0, base, hot)
        x = write_label(win, h - 1, x, " zoom out  ", 0, base, base)
        x = write_label(win, h - 1, x, "0", 0, base, hot)
        x = write_label(win, h - 1, x, " reset  ", 0, base, base)
        x = write_label(win, h - 1, x, "s", 0, base, hot)
        x = write_label(win, h - 1, x, " seconds  ", 0, base, base)
        # `a` toggles the freeze-and-analyse overlay. The header line
        # already carries [PAUSED] when the recorders are frozen, so
        # we keep the footer hint short.
        x = write_label(win, h - 1, x, "a", 0, base, hot)
        x = write_label(win, h - 1, x, " analyse  ", 0, base, base)
        # SmartMet-only toggle — show the active state inline so the
        # operator does not need to glance up at the header.
        s_label = "S" + ("●" if self.smartmet_only else "○")
        x = write_label(win, h - 1, x, s_label[:1], 0, base, hot)
        x = write_label(win, h - 1, x, s_label[1:] + " smartmet-only  ",
                        0, base, base)
        # Thread-class cycle — render the active class's first letter
        # so the operator can read the cycle position at a glance.
        t_letter = self.thread_class[:1].upper()  # A / R / B
        x = write_label(win, h - 1, x, "T", 0, base, hot)
        x = write_label(win, h - 1, x, f"({t_letter}) thread  ",
                        0, base, base)
        # Mode keys, with the active mode's letter shown in reverse
        # video so the operator can see where they are at a glance.
        for ch, mode in (("C", "on-cpu"), ("B", "off-cpu"),
                         ("L", "off-cpu-locks"), ("M", "pagefault"),
                         ("W", "wakeup"), ("I", "blockflame"),
                         ("A", "malloc")):
            attr = (curses.A_REVERSE | curses.A_BOLD if mode == self.mode
                    else hot)
            x = write_label(win, h - 1, x, ch, 0, base, attr)
            x = write_label(win, h - 1, x, " ", 0, base, base)
        x = write_label(win, h - 1, x, f"({self.mode}) ", 0, base, base)
        if n_procs > 1:
            x = write_label(win, h - 1, x, "  ", 0, base, base)
            x = write_label(win, h - 1, x, "n", 0, base, hot)
            x = write_label(win, h - 1, x, "/", 0, base, base)
            x = write_label(win, h - 1, x, "N", 0, base, hot)
            x = write_label(win, h - 1, x, " PID", 0, base, base)
        if x < w - 1:
            safe_addstr(win, h - 1, x, " " * (w - x - 1), base)

    def _draw_seconds_menu(self, win, store) -> None:
        """Centered modal overlay listing the preset record durations."""
        h, w = win.getmaxyx()
        title = " perf record duration "
        item_text_width = 24
        menu_w = max(item_text_width, len(title)) + 4
        # title row + blank + items + blank + footer
        menu_h = len(PERF_SECONDS_PRESETS) + 4
        if menu_h >= h or menu_w >= w:
            return  # terminal too small; skip the overlay
        top = max(0, (h - menu_h) // 2)
        left = max(0, (w - menu_w) // 2)
        bg = theme.attr(theme.P_TITLE)
        # Wipe the rectangle so the flame underneath doesn't bleed
        # through and confuse the eye.
        for y in range(top, top + menu_h):
            safe_addstr(win, y, left, " " * menu_w, bg)
        # Title
        safe_addstr(win, top, left, title.center(menu_w),
                    theme.attr(theme.P_TAB_ACTIVE, curses.A_BOLD))
        current = getattr(store, "perf_record_seconds", 3)
        for i, sec in enumerate(PERF_SECONDS_PRESETS):
            row_y = top + 2 + i
            is_cursor = (i == self._seconds_menu_idx)
            attr = (theme.attr(theme.P_HIGHLIGHT, curses.A_BOLD)
                    if is_cursor else bg)
            mark = "●" if sec == current else " "
            label = f"  {mark} {sec:>3} second{'s' if sec != 1 else ' '}  "
            safe_addstr(win, row_y, left + 2,
                        label.ljust(menu_w - 4), attr)
        footer = " ↑↓ select  Enter apply  Esc cancel "
        safe_addstr(win, top + menu_h - 1, left, footer.center(menu_w),
                    theme.attr(theme.P_DIM))

    def _draw_findings_overlay(self, win, store) -> None:
        """Centered modal listing the analyser's findings.

        Layout: title row, then one row per finding (severity badge +
        share% + title), then a separator, then the selected finding's
        hint wrapped over the available width, then a footer of key
        hints. The whole thing is sized to fit the longest finding +
        the hint on a single line, with a hard cap at 80% of screen
        width so the underlying flame remains visible at the edges.
        """
        h, w = win.getmaxyx()
        if not self._findings:
            # Empty case: short modal explaining what happened. Lets
            # the operator know analyse ran successfully and just
            # found nothing actionable, vs. silently doing nothing.
            menu_w = min(60, max(40, w - 4))
            menu_h = 7
            if menu_h >= h or menu_w >= w:
                return
            top = max(0, (h - menu_h) // 2)
            left = max(0, (w - menu_w) // 2)
            bg = theme.attr(theme.P_TITLE)
            for y in range(top, top + menu_h):
                safe_addstr(win, y, left, " " * menu_w, bg)
            safe_addstr(win, top, left,
                        " analyse — no findings ".center(menu_w),
                        theme.attr(theme.P_TAB_ACTIVE, curses.A_BOLD))
            safe_addstr(win, top + 2, left + 2,
                        "Detectors found nothing above threshold.",
                        theme.attr(theme.P_DIM))
            safe_addstr(win, top + 3, left + 2,
                        "Recorders are paused on the current rings.",
                        theme.attr(theme.P_DIM))
            safe_addstr(win, top + 4, left + 2,
                        "Press 'a' to resume, Esc to dismiss this box.",
                        theme.attr(theme.P_DIM))
            safe_addstr(win, top + menu_h - 1, left,
                        " a resume  Esc dismiss ".center(menu_w),
                        theme.attr(theme.P_DIM))
            return

        # Sized model: at most 12 findings on screen at once (matches
        # the panel-help convention for "top X" lists), plus 5 chrome
        # rows (title + blank + separator + 2-row hint + footer).
        max_visible = min(12, len(self._findings))
        menu_w = min(max(60, w - 8), int(w * 0.8))
        menu_h = max_visible + 6
        if menu_h >= h or menu_w >= w:
            return
        top = max(0, (h - menu_h) // 2)
        left = max(0, (w - menu_w) // 2)

        bg = theme.attr(theme.P_TITLE)
        for y in range(top, top + menu_h):
            safe_addstr(win, y, left, " " * menu_w, bg)

        title = f" analyse — {len(self._findings)} finding(s) "
        safe_addstr(win, top, left, title.center(menu_w),
                    theme.attr(theme.P_TAB_ACTIVE, curses.A_BOLD))

        # Findings list. Scrolling: when the cursor is past the visible
        # window, slide the visible slice. Simple top-anchored scroll
        # is enough — the list is at most ~6 entries in practice.
        visible_top = 0
        if self._findings_idx >= max_visible:
            visible_top = self._findings_idx - max_visible + 1
        for i in range(max_visible):
            idx = visible_top + i
            if idx >= len(self._findings):
                break
            f = self._findings[idx]
            row_y = top + 2 + i
            is_cursor = (idx == self._findings_idx)
            row_attr = (theme.attr(theme.P_HIGHLIGHT, curses.A_BOLD)
                        if is_cursor else bg)
            sev_attr = self._severity_attr(f.severity)
            sev_badge = f"[{f.severity.upper():>4}]"
            line = f" {sev_badge}  {f.share_pct:>5.1f}%  {f.title}"
            line = line[:menu_w - 4]
            safe_addstr(win, row_y, left + 2,
                        line.ljust(menu_w - 4),
                        sev_attr if not is_cursor else row_attr)

        # Selected finding's hint, wrapped over two rows.
        hint_y = top + 2 + max_visible + 1
        if 0 <= self._findings_idx < len(self._findings):
            sel = self._findings[self._findings_idx]
            hint_w = menu_w - 6
            hint = sel.hint
            # Naive wrap: break at the last space before hint_w.
            line1, line2 = hint, ""
            if len(hint) > hint_w:
                cut = hint.rfind(" ", 0, hint_w)
                if cut == -1:
                    cut = hint_w
                line1 = hint[:cut]
                line2 = hint[cut:].lstrip()[:hint_w]
            safe_addstr(win, hint_y, left + 3, line1,
                        theme.attr(theme.P_DIM))
            if line2:
                safe_addstr(win, hint_y + 1, left + 3, line2,
                            theme.attr(theme.P_DIM))

        footer = " ↑↓ select  Enter zoom to evidence  Esc dismiss  a resume "
        safe_addstr(win, top + menu_h - 1, left, footer.center(menu_w),
                    theme.attr(theme.P_DIM))

    def _severity_attr(self, severity: str) -> int:
        """Colour findings by severity. Reuses the existing palette
        slots so we don't need a new colour pair: P_BAD for high,
        P_ACCENT for med, P_DIM for low."""
        if severity == SEV_HIGH:
            return theme.attr(theme.P_BAD, curses.A_BOLD)
        if severity == SEV_MED:
            return theme.attr(theme.P_ACCENT, curses.A_BOLD)
        return theme.attr(theme.P_DIM)
