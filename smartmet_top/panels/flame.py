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
"""

from __future__ import annotations

import curses
from typing import Dict, List, Optional, Tuple

from .. import theme
from ..state.store import ProcInfo
from ..widgets.bars import human_count, sparkline
from .base import Panel, safe_addstr, write_label, write_row
from .proc import _build_flame_tree, _flame_color


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
        "n/N next/prev PID. Requires --perf."
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

    # ---- key handling ------------------------------------------------------

    def handle_key(self, key, store):
        # PID switching is allowed regardless of perf state.
        procs = store.proc_list()
        pids = [p.pid for p in procs]
        if pids:
            selected = store.proc_selected()
            if selected is None or selected not in pids:
                store.proc_select(pids[0])
                selected = pids[0]
            if key == ord("n"):
                store.proc_select(pids[(pids.index(selected) + 1) % len(pids)])
                return True
            if key == ord("N"):
                store.proc_select(pids[(pids.index(selected) - 1) % len(pids)])
                return True
        # Flame navigation only meaningful when perf is on AND we have data.
        if not store.perf_enabled or not self._last_root:
            return False
        if key in (curses.KEY_RIGHT, ord("l")):
            self._move_sibling(+1)
        elif key in (curses.KEY_LEFT, ord("h")):
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
            selected = procs[0].pid
            store.proc_select(selected)
        info = next((p for p in procs if p.pid == selected), procs[0])

        self._draw_header(win, info, store, len(procs))
        flame_bottom = self._draw_flame_section(win, store, info)
        self._draw_top_symbols(win, store, info, flame_bottom)
        self._draw_footer(win, n_procs=len(procs))

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

    def _draw_header(self, win, info: ProcInfo, store, n_procs: int) -> None:
        h, w = win.getmaxyx()
        sample_count = store.perf_last_sample_count(info.pid)
        breadcrumb = " > ".join(self.zoom_path) if self.zoom_path else "(root)"
        header = (
            f" Flame — smartmetd[{info.pid}]  {info.role}  "
            f"status={store.perf_status}  last={sample_count}  "
            f"zoom={breadcrumb}"
        )
        safe_addstr(win, 0, 0, header.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

    def _draw_flame_section(self, win, store, info: ProcInfo) -> int:
        """Render the flame tree. Returns the bottom row index it used so
        the top-symbols list knows where to start."""
        h, w = win.getmaxyx()
        # If a previous cycle failed, surface that in place of the flame.
        if store.perf_last_error:
            self._draw_perf_error(win, 1, h - 2, store.perf_last_error)
            self._last_frames = []
            self._last_root = {}
            return h - 2
        stacks = store.perf_recent_stacks(info.pid)
        if not stacks:
            safe_addstr(win, 1, 2,
                        "no stack samples yet — waiting for first perf cycle…",
                        theme.attr(theme.P_DIM))
            self._last_frames = []
            self._last_root = {}
            return 2
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
        flame_top = 1
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

    def _draw_top_symbols(self, win, store, info: ProcInfo,
                          start_row: int) -> None:
        h, w = win.getmaxyx()
        if start_row >= h - 2:
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
        x = write_label(win, h - 1, x, " reset zoom", 0, base, base)
        if n_procs > 1:
            x = write_label(win, h - 1, x, "  ", 0, base, base)
            x = write_label(win, h - 1, x, "n", 0, base, hot)
            x = write_label(win, h - 1, x, "/", 0, base, base)
            x = write_label(win, h - 1, x, "N", 0, base, hot)
            x = write_label(win, h - 1, x, " PID", 0, base, base)
        if x < w - 1:
            safe_addstr(win, h - 1, x, " " * (w - x - 1), base)
