"""Help overlay / panel."""

from __future__ import annotations

import curses

from .base import Panel, safe_addstr


KEYS = [
    ("Tab / Shift-Tab",   "switch panel"),
    ("1..6",              "jump to panel by number"),
    ("? or F1",           "toggle this help"),
    ("q / Ctrl-C",        "quit"),
    ("↑ ↓ j k",           "move cursor"),
    ("PgUp / PgDn",       "page"),
    ("Home g / End G",    "top / bottom"),
    ("Enter",             "drill in (URLs panel)"),
    ("Esc / h / ←",       "back from drill-in; clear filter"),
    ("/",                 "filter (URLs / Logs)"),
    ("s / S",             "cycle sort column forward/back"),
    ("r",                 "reverse sort"),
    ("[ / ]",             "shrink / grow time window (1/5/15/60 min)"),
]


class HelpPanel(Panel):
    name = "Help"
    hotkey = "?"
    help_text = "Keyboard reference."

    def draw(self, win, store):
        h, w = win.getmaxyx()
        safe_addstr(win, 0, 0, " Help".ljust(w - 1), curses.A_REVERSE)
        safe_addstr(win, 2, 2, "Keys", curses.A_BOLD)
        row = 3
        for k, v in KEYS:
            safe_addstr(win, row, 2, f"  {k:<22} {v}")
            row += 1

        row += 1
        safe_addstr(win, row, 2, "Data sources", curses.A_BOLD)
        row += 1
        safe_addstr(win, row, 2, f"  log tail: {store.logtail_status}")
        row += 1
        safe_addstr(win, row, 2, f"  admin:    {store.admin_status}")
        row += 2
        safe_addstr(win, row, 2, "Press ? again to return to the previous panel.")
