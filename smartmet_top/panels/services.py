"""Services panel — admin-plugin ?what=servicestats."""

from __future__ import annotations

import curses
import time

from ..widgets.bars import hbar, human_count, human_ms
from .base import Panel, safe_addstr


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class ServicesPanel(Panel):
    name = "Services"
    hotkey = "4"
    help_text = "Per-handler throughput from /admin?what=servicestats."

    def __init__(self):
        self.cursor = 0
        self.scroll = 0

    def handle_key(self, key, store):
        if key in (curses.KEY_UP, ord("k")):
            self.cursor = max(0, self.cursor - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.cursor += 1
        elif key == curses.KEY_PPAGE:
            self.cursor = max(0, self.cursor - 10)
        elif key == curses.KEY_NPAGE:
            self.cursor += 10
        return None

    def draw(self, win, store):
        h, w = win.getmaxyx()
        snap = store.servicestats
        age = f"{time.time() - snap.fetched_at:.1f}s ago" if snap.fetched_at else "never"
        safe_addstr(win, 0, 0,
                    f" Services — {'OK' if snap.ok else 'ERROR'}  fetched {age}".ljust(w - 1),
                    curses.A_REVERSE)
        if not snap.ok:
            safe_addstr(win, 2, 2, f"error: {snap.error}")
            return
        rows = snap.rows or []
        if not rows:
            safe_addstr(win, 2, 2, "no service data available yet")
            return

        # compute max for scaling
        mx1 = max((_f(r.get("LastMinute")) for r in rows), default=0.0)

        safe_addstr(win, 2, 0,
                    f"{'handler':<40} {'req/min':>8} {'req/h':>8} {'req/d':>10} "
                    f"{'avg_ms':>8}  {'last min':<25}", curses.A_BOLD)
        safe_addstr(win, 3, 0, "─" * (w - 1))

        body_top = 4
        body_h = h - body_top - 1
        if body_h <= 0:
            return

        if self.cursor >= len(rows):
            self.cursor = len(rows) - 1
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        if self.cursor >= self.scroll + body_h:
            self.scroll = self.cursor - body_h + 1

        visible = rows[self.scroll : self.scroll + body_h]
        for i, r in enumerate(visible):
            handler = str(r.get("Handler") or r.get("handler") or "?")
            m1 = _f(r.get("LastMinute"))
            m60 = _f(r.get("LastHour"))
            d24 = _f(r.get("Last24Hours"))
            avg = _f(r.get("AverageDuration"))
            line = (
                f"{handler[:40]:<40} "
                f"{m1:>8.1f} "
                f"{m60:>8.1f} "
                f"{d24:>10.1f} "
                f"{human_ms(avg):>8}  "
                f"{hbar(m1, mx1, 25)}"
            )
            attr = curses.A_REVERSE if self.scroll + i == self.cursor else 0
            safe_addstr(win, body_top + i, 0, line, attr)
