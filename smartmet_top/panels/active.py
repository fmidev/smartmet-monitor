"""Active requests panel — admin-plugin ?what=activerequests."""

from __future__ import annotations

import curses
import time

from .. import theme
from .base import Panel, safe_addstr, write_row


class ActivePanel(Panel):
    name = "Active"
    hotkey = "5"
    help_text = "In-flight requests from /admin?what=activerequests."

    def __init__(self):
        self.scroll = 0

    def handle_key(self, key, store):
        if key in (curses.KEY_UP, ord("k")):
            self.scroll = max(0, self.scroll - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.scroll += 1
        elif key == curses.KEY_PPAGE:
            self.scroll = max(0, self.scroll - 10)
        elif key == curses.KEY_NPAGE:
            self.scroll += 10
        return None

    def draw(self, win, store):
        h, w = win.getmaxyx()
        snap = store.activerequests
        age = f"{time.time() - snap.fetched_at:.1f}s ago" if snap.fetched_at else "never"
        hdr_attr = theme.attr(theme.P_TAB_ACTIVE) if snap.ok else theme.attr(theme.P_BAD, curses.A_BOLD)
        safe_addstr(win, 0, 0,
                    f" Active — {'OK' if snap.ok else 'ERROR'}  fetched {age}".ljust(w - 1),
                    hdr_attr)
        if not snap.ok:
            safe_addstr(win, 2, 2, f"error: {snap.error}", theme.attr(theme.P_BAD))
            return
        rows = snap.rows or []
        if not rows:
            safe_addstr(win, 2, 2, "no active requests", theme.attr(theme.P_DIM))
            return

        # sort by descending duration (long-running first)
        def dur(r):
            try:
                return float(r.get("Duration") or r.get("duration") or 0)
            except (ValueError, TypeError):
                return 0.0

        rows = sorted(rows, key=dur, reverse=True)
        safe_addstr(win, 2, 0,
                    f"{'id':>6} {'dur_s':>7} {'client':<20} {'apikey':<20}  request",
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        safe_addstr(win, 3, 0, "─" * (w - 1), theme.attr(theme.P_DIM))

        body_top = 4
        body_h = h - body_top - 1
        if self.scroll >= len(rows):
            self.scroll = max(0, len(rows) - 1)

        for i, r in enumerate(rows[self.scroll : self.scroll + body_h]):
            rid = str(r.get("Id") or r.get("id") or "?")
            d = dur(r)
            cip = str(r.get("ClientIP") or r.get("clientip") or "?")
            ak = str(r.get("Apikey") or r.get("apikey") or "-")
            req = str(r.get("RequestString") or r.get("requeststring") or "")
            dur_attr = theme.duration_color(d)
            cells = [
                (f"{rid:>6} ", 0),
                (f"{d:>7.1f} ", dur_attr),
                (f"{cip[:20]:<20} ", 0),
                (f"{ak[:20]:<20}  ", theme.attr(theme.P_ACCENT)),
                (req, 0),
            ]
            write_row(win, body_top + i, 0, cells)
