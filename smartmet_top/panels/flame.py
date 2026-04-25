"""Full-screen flamegraph panel.

Reuses the tree builder + renderer from `panels.proc` and gives them
the entire terminal so the boxes are wide enough to read function
names. The Proc panel keeps its inline flamegraph toggle (`f` while
focused on Proc) for the case where the operator wants to compare
hot symbols against memory growth at the same time; this view is for
the case where stacks are deep and width matters.
"""

from __future__ import annotations

import curses

from .. import theme
from ..state.store import ProcInfo
from .base import Panel, safe_addstr, write_label
from .proc import _build_flame_tree, _render_flame_level


class FlamePanel(Panel):
    name = "Flame"
    hotkey = "f"
    help_text = (
        "Live flamegraph for the focused smartmetd PID. "
        "n/N next/prev process. Requires --perf."
    )

    def handle_key(self, key, store):
        procs = store.proc_list()
        if not procs:
            return False
        pids = [p.pid for p in procs]
        selected = store.proc_selected()
        if selected is None or selected not in pids:
            selected = pids[0]
            store.proc_select(selected)
        if key == ord("n"):
            i = pids.index(selected)
            store.proc_select(pids[(i + 1) % len(pids)])
        elif key == ord("N"):
            i = pids.index(selected)
            store.proc_select(pids[(i - 1) % len(pids)])
        else:
            return False
        return True

    def export_snapshot(self, store):
        # Flamegraphs are inherently hierarchical — exporting a flat
        # CSV would lose the structure. Decline cleanly so the operator
        # gets an informative toast instead of garbage.
        return None, None

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
        self._draw_flame(win, store, info)
        self._draw_footer(win, n_procs=len(procs))

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
        safe_addstr(win, 7, 2,
                    "Sampling spawns `perf record -F 99 -g -p PID -- sleep 1` "
                    "every --perf-interval seconds (default 10s).",
                    theme.attr(theme.P_DIM))

    def _draw_header(self, win, info: ProcInfo, store, n_procs: int) -> None:
        h, w = win.getmaxyx()
        sample_count = store.perf_last_sample_count(info.pid)
        header = (
            f" Flame — smartmetd[{info.pid}]  role={info.role}  "
            f"status={store.perf_status}  last={sample_count} samples  "
            f"({n_procs} smartmetd PID{'s' if n_procs != 1 else ''})"
        )
        safe_addstr(win, 0, 0, header.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

    def _draw_flame(self, win, store, info: ProcInfo) -> None:
        h, w = win.getmaxyx()
        # Reserve row 0 for header, last row for footer hint.
        top = 1
        max_y = h - 2
        if max_y <= top:
            return
        stacks = store.perf_recent_stacks(info.pid)
        if not stacks:
            safe_addstr(win, top, 2,
                        "no stack samples yet — waiting for first perf cycle…",
                        theme.attr(theme.P_DIM))
            return
        recent = stacks[-2000:]
        tree = _build_flame_tree(recent)
        if not tree:
            return
        total = sum(v[0] for v in tree.values()) or 1
        _render_flame_level(win, top, max_y, 0, w - 1, tree, total)

    def _draw_footer(self, win, n_procs: int) -> None:
        h, w = win.getmaxyx()
        if h < 2:
            return
        hot = theme.attr(theme.P_MNEMONIC, curses.A_BOLD | curses.A_UNDERLINE)
        base = theme.attr(theme.P_TITLE)
        x = 0
        safe_addstr(win, h - 1, 0, " ", base); x += 1
        if n_procs > 1:
            x = write_label(win, h - 1, x, "n", 0, base, hot)
            x = write_label(win, h - 1, x, "ext / ", 0, base, base)
            x = write_label(win, h - 1, x, "N", 0, base, hot)
            x = write_label(win, h - 1, x, " prev    ", 0, base, base)
        x = write_label(win, h - 1, x,
                        "rebuilt every perf cycle from last 2000 stacks",
                        0, base, base)
        if x < w - 1:
            safe_addstr(win, h - 1, x, " " * (w - x - 1), base)
