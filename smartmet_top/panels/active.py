"""Active requests panel — admin-plugin ?what=activerequests."""

from __future__ import annotations

import curses
import time

from .base import Panel, safe_addstr


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
        safe_addstr(win, 0, 0,
                    f" Active — {'OK' if snap.ok else 'ERROR'}  fetched {age}".ljust(w - 1),
                    curses.A_REVERSE)
        if not snap.ok:
            safe_addstr(win, 2, 2, f"error: {snap.error}")
            return
        rows = snap.rows or []
        if not rows:
            safe_addstr(win, 2, 2, "no active requests")
            return

        # sort by descending duration (long-running first)
        def dur(r):
            try:
                return float(r.get("Duration") or r.get("duration") or 0)
            except (ValueError, TypeError):
                return 0.0

        rows = sorted(rows, key=dur, reverse=True)
        safe_addstr(win, 2, 0,
                    f"{'id':>6} {'dur_s':>7} {'client':<20} {'apikey':<20}  request", curses.A_BOLD)
        safe_addstr(win, 3, 0, "─" * (w - 1))

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
            line = f"{rid:>6} {d:>7.1f} {cip[:20]:<20} {ak[:20]:<20}  {req}"
            attr = curses.A_BOLD if d > 10 else 0
            safe_addstr(win, body_top + i, 0, line, attr)
