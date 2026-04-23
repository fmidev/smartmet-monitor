"""Services panel — admin-plugin ?what=servicestats."""

from __future__ import annotations

import curses
import time

from .. import theme
from ..widgets.bars import hbar, human_count, human_ms, sparkline
from .base import Panel, safe_addstr, write_row


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
        hdr_attr = theme.attr(theme.P_TAB_ACTIVE) if snap.ok else theme.attr(theme.P_BAD, curses.A_BOLD)
        safe_addstr(win, 0, 0,
                    f" Services — {'OK' if snap.ok else 'ERROR'}  fetched {age}".ljust(w - 1),
                    hdr_attr)
        if not snap.ok:
            safe_addstr(win, 2, 2, f"error: {snap.error}", theme.attr(theme.P_BAD))
            return
        rows = snap.rows or []
        if not rows:
            safe_addstr(win, 2, 2, "no service data available yet", theme.attr(theme.P_DIM))
            return

        # compute max for scaling
        mx1 = max((_f(r.get("LastMinute")) for r in rows), default=0.0)

        safe_addstr(win, 2, 0,
                    f"{'handler':<40} {'req/min':>8} {'req/h':>8} {'req/d':>10} "
                    f"{'avg_ms':>8}  {'last min':<25}  {'trend':<20}",
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        safe_addstr(win, 3, 0, "─" * (w - 1), theme.attr(theme.P_DIM))

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
            row_attr = curses.A_REVERSE if self.scroll + i == self.cursor else 0
            trend = store.service_history.series(handler, "req_per_min", samples=20)
            trend_str = sparkline(trend, width=20) if trend else " " * 20
            cells = [
                (f"{handler[:40]:<40} ", 0),
                (f"{m1:>8.1f} ", 0),
                (f"{m60:>8.1f} ", 0),
                (f"{d24:>10.1f} ", 0),
                (f"{human_ms(avg):>8}  ", theme.latency_color(avg)),
                (hbar(m1, mx1, 25), theme.attr(theme.P_SPARK)),
                ("  ", 0),
                (trend_str, theme.attr(theme.P_SPARK)),
            ]
            write_row(win, body_top + i, 0, cells, row_attr=row_attr)
