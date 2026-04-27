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


# Valid Flame view modes. Selection is direct via the C/B/L/M keys
# in the panel's handle_key — no cycling. Order here is purely the
# logical workflow ordering (running → blocked → locks → memory)
# used by the README's "What's it for?" diagrams.
_MODES = ("on-cpu", "off-cpu", "off-cpu-locks", "pagefault")


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
        "(blocked), L locks, M memory faults. Requires --perf."
    )

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

    # ---- key handling ------------------------------------------------------

    def handle_key(self, key, store):
        # While the duration overlay is open, every keystroke goes there
        # — including arrows that would otherwise navigate the flame.
        if self._seconds_menu_open:
            return self._handle_seconds_menu_key(key, store)

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

    def _zoom_out(self) -> None:
        if not self.zoom_path:
            return
        self.zoom_path = self.zoom_path[:-1]
        # Pull the cursor up so it stays inside the visible tree.
        if len(self.cursor_path) < len(self.zoom_path):
            self.cursor_path = self.zoom_path

    # ---- export ------------------------------------------------------------

    def export_snapshot(self, store):
        # Hierarchical — flat CSV would lose the structure.
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
        # Draw the modal overlay last so it sits on top of the flame.
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
        if self.mode == "off-cpu":
            total_us = store.offcpu_last_total_us(info.pid)
            ms = total_us // 1000
            header = (
                f" Flame off-CPU — pid={info.pid}  "
                f"status={store.offcpu_status}  "
                f"last_off={ms} ms  zoom={breadcrumb}"
            )
        elif self.mode == "off-cpu-locks":
            total_us = store.offcpu_last_total_us(info.pid)
            ms = total_us // 1000
            header = (
                f" Flame off-CPU (locks only) — pid={info.pid}  "
                f"status={store.offcpu_status}  "
                f"last_off={ms} ms  zoom={breadcrumb}"
            )
        elif self.mode == "pagefault":
            sample_count = store.pagefault_last_sample_count(info.pid)
            header = (
                f" Flame page-faults — pid={info.pid}  "
                f"status={store.pagefault_status}  "
                f"last={sample_count} faults  zoom={breadcrumb}"
            )
        else:
            sample_count = store.perf_last_sample_count(info.pid)
            header = (
                f" Flame on-CPU — pid={info.pid}  status={store.perf_status}  "
                f"last={sample_count}  zoom={breadcrumb}"
            )
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
        # Mode keys, with the active mode's letter shown in reverse
        # video so the operator can see where they are at a glance.
        for ch, mode in (("C", "on-cpu"), ("B", "off-cpu"),
                         ("L", "off-cpu-locks"), ("M", "pagefault")):
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
