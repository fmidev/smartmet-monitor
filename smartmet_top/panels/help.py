"""Help overlay / panel."""

from __future__ import annotations

import curses

from .. import theme
from .base import Panel, safe_addstr


KEYS = [
    ("Tab / Shift-Tab",   "cycle panels forward/back"),
    ("i o g u c s a p l k", "jump to view / panel by red letter in tab"),
    ("? or F1",           "toggle this help"),
    ("q / Ctrl-C",        "quit"),
    ("↑ ↓ ← →",            "move cursor / navigate (arrows only)"),
    ("PgUp / PgDn",       "page"),
    ("Home / End",        "top / bottom"),
    ("Enter",             "drill in (URLs / Apikeys / Graphs)"),
    ("Esc / ←",           "back from drill-in; clear filter"),
    ("[ / ]",             "shrink / grow time window (1/5/15/60 min)"),
    ("/",                 "filter (URLs / Apikeys / Logs / Graphs)"),
    ("s / S",             "cycle sort column forward/back"),
    ("r",                 "reverse sort"),
    ("e / E",             "export current panel as CSV / JSON"),
    ("n / N",             "next / prev smartmetd PID (Proc / Flame)"),
    ("1 - 9",             "select PID directly by index (Proc / Flame)"),
    ("f",                 "toggle inline flamegraph (Proc); zoom-in (Flame)"),
]


class HelpPanel(Panel):
    name = "Help"
    hotkey = "?"
    help_text = "Keyboard reference."

    def draw(self, win, store):
        h, w = win.getmaxyx()
        safe_addstr(win, 0, 0, " Help".ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))
        safe_addstr(win, 2, 2, "Keys", theme.attr(theme.P_HEADER, curses.A_BOLD))
        row = 3
        for k, v in KEYS:
            safe_addstr(win, row, 2, f"  {k:<22}", theme.attr(theme.P_ACCENT))
            safe_addstr(win, row, 28, v)
            row += 1

        row += 1
        safe_addstr(win, row, 2, "Data sources",
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        row += 1
        safe_addstr(win, row, 2, f"  log tail: {store.logtail_status}")
        for host in store.admin_hosts:
            row += 1
            s = store.admin_status.get(host, "?")
            role = store.host_role.get(host, "unknown")
            whats = len(store.available_what.get(host, set()))
            safe_addstr(win, row, 2,
                        f"  admin[{host}] role={role} what-count={whats}  {s}")
        if not store.admin_hosts:
            row += 1
            safe_addstr(win, row, 2, "  admin:    (no hosts configured)",
                        theme.attr(theme.P_DIM))
        row += 2
        safe_addstr(win, row, 2, "Press ? again to return to the previous panel.",
                    theme.attr(theme.P_DIM))
