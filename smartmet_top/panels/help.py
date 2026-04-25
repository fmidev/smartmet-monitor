"""Help overlay / panel."""

from __future__ import annotations

import curses

from .. import theme
from .base import Panel, safe_addstr


KEYS = [
    ("Tab / Shift-Tab",   "cycle panels forward/back"),
    ("o g u c s a p l k", "jump straight to panel (red letter in tab)"),
    ("? or F1",           "toggle this help"),
    ("q / Ctrl-C",        "quit"),
    ("↑ ↓ j k",           "move cursor"),
    ("PgUp / PgDn",       "page"),
    ("Home g / End G",    "top / bottom"),
    ("Enter",             "drill in (URLs / Keys panels)"),
    ("Esc / b / ←",       "back from drill-in; clear filter"),
    ("j / k  n / p",      "next / prev entry while drilled in"),
    ("[ / ]",             "shrink / grow time window (1/5/15/60 min)"),
    ("h / t / y",         "toggle histogram / status / keys sections (URLs drill-in)"),
    ("/",                 "filter (URLs / Keys / Logs)"),
    ("s / S",             "cycle sort column forward/back (inside URLs/Keys)"),
    ("r",                 "reverse sort"),
    ("e / E",             "export current panel as CSV / JSON"),
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
