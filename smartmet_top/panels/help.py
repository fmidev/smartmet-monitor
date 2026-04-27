"""Help overlay / panel.

When `?` (or F1) is pressed, the App swaps the active panel for this
one. We render the active panel's `panel_help` first so the operator
gets *contextual* help — what every section / column / sparkline on
that panel measures — before the global keys reference. Pressing `?`
again returns to the panel.
"""

from __future__ import annotations

import curses

from .. import theme
from .base import Panel, safe_addstr


KEYS = [
    ("Tab / Shift-Tab",   "cycle panels forward/back"),
    ("i o g u c s a p l k n", "jump to view / panel by red letter in tab"),
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
    ("C / B / L / M / W / I / A", "Flame mode: on-CPU / off-CPU / locks / "
                                  "memory faults / wakeup / block-I/O / alloc"),
    ("+ / -",             "grow / shrink sparkline height (Proc, Network)"),
    ("!",                 "alerts overlay"),
]


class HelpPanel(Panel):
    name = "Help"
    hotkey = "?"
    help_text = "Keyboard reference and panel-specific notes."

    def __init__(self, app=None):
        # `app` lets the help panel ask "which panel was active before
        # ? was pressed?" so we can render its contextual help first.
        # Optional so HelpPanel still works in unit tests.
        self.app = app

    def draw(self, win, store):
        h, w = win.getmaxyx()
        # Find the contextual panel — the one that was active when ?
        # was pressed. App.show_help controls the swap, panel_idx is
        # untouched, so panels[panel_idx] is the previous panel.
        prev = None
        if self.app is not None:
            try:
                prev = self.app.panels[self.app.panel_idx]
            except (AttributeError, IndexError):
                prev = None

        title = " Help"
        if prev is not None:
            title = f" Help — {prev.name}"
        safe_addstr(win, 0, 0, title.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

        row = 2
        # Contextual help for the panel that was active. Rendered
        # FIRST because that's what the operator most likely wanted
        # when they pressed `?`. Falls back gracefully when a panel
        # has no panel_help defined (most don't yet — the perf-heavy
        # ones do).
        if prev is not None and prev.panel_help.strip():
            safe_addstr(win, row, 2, f"What's on the {prev.name} panel",
                        theme.attr(theme.P_HEADER, curses.A_BOLD))
            row += 2
            for line in prev.panel_help.splitlines():
                if row >= h - 4:
                    break
                # Section headings end with `:` and are short
                # (e.g. "Columns:", "Memory:", "Keys:"). Render
                # those bold; everything else as body text.
                stripped = line.strip()
                is_heading = (stripped.endswith(":")
                              and 0 < len(stripped) <= 40
                              and " " not in stripped[:-1].lstrip()[:0]
                              # at most a few words before the colon —
                              # weeds out sentences that happen to end
                              # with `:`.
                              and stripped[:-1].count(" ") <= 4)
                attr = (theme.attr(theme.P_HEADER, curses.A_BOLD)
                        if is_heading else 0)
                safe_addstr(win, row, 4, line, attr)
                row += 1
            row += 1
        else:
            if prev is not None:
                safe_addstr(win, row, 2,
                            f"(The {prev.name} panel has no contextual "
                            f"help written yet.)",
                            theme.attr(theme.P_DIM))
                row += 2

        # Global key reference. Always present.
        if row < h - 2:
            safe_addstr(win, row, 2, "Keys",
                        theme.attr(theme.P_HEADER, curses.A_BOLD))
            row += 1
            for k, v in KEYS:
                if row >= h - 2:
                    break
                safe_addstr(win, row, 2, f"  {k:<26}",
                            theme.attr(theme.P_ACCENT))
                safe_addstr(win, row, 32, v[: max(0, w - 34)])
                row += 1

        if row < h - 2:
            row += 1
            safe_addstr(win, row, 2, "Data sources",
                        theme.attr(theme.P_HEADER, curses.A_BOLD))
            row += 1
            if row < h - 1:
                safe_addstr(win, row, 2, f"  log tail: {store.logtail_status}")
            for host in store.admin_hosts:
                row += 1
                if row >= h - 1:
                    break
                s = store.admin_status.get(host, "?")
                role = store.host_role.get(host, "unknown")
                whats = len(store.available_what.get(host, set()))
                safe_addstr(win, row, 2,
                            f"  admin[{host}] role={role} "
                            f"what-count={whats}  {s}")
            if not store.admin_hosts:
                row += 1
                if row < h - 1:
                    safe_addstr(win, row, 2,
                                "  admin:    (no hosts configured)",
                                theme.attr(theme.P_DIM))

        if h > 2:
            safe_addstr(win, h - 2, 2,
                        "Press ? to return to the previous panel.",
                        theme.attr(theme.P_DIM))
