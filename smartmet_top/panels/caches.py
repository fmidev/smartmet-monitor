"""Caches panel — admin-plugin ?what=cachestats."""

from __future__ import annotations

import curses
import time

from .. import theme
from ..widgets.bars import hbar, human_count, sparkline
from .base import Panel, safe_addstr, write_row


def _as_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


class CachesPanel(Panel):
    name = "Caches"
    hotkey = "3"
    help_text = "Cache stats polled from /admin?what=cachestats."

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
        snap = store.cachestats
        age = f"{time.time() - snap.fetched_at:.1f}s ago" if snap.fetched_at else "never"
        hdr_attr = theme.attr(theme.P_TAB_ACTIVE) if snap.ok else theme.attr(theme.P_BAD, curses.A_BOLD)
        safe_addstr(win, 0, 0,
                    f" Caches — {'OK' if snap.ok else 'ERROR'}  fetched {age}".ljust(w - 1),
                    hdr_attr)
        if not snap.ok:
            safe_addstr(win, 2, 2, f"error: {snap.error}", theme.attr(theme.P_BAD))
            return
        rows = snap.rows or []
        if not rows:
            safe_addstr(win, 2, 2, "no cache data available yet", theme.attr(theme.P_DIM))
            return

        # header
        safe_addstr(win, 2, 0,
                    f"{'cache':<34} {'size':>9} {'max':>9} {'hits/m':>9} {'ins/m':>9} "
                    f"{'hit%':>6}  {'hitrate':<20}  {'hit/m trend':<20}",
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        safe_addstr(win, 3, 0, "─" * (w - 1), theme.attr(theme.P_DIM))

        body_top = 4
        body_h = h - body_top - 1
        if body_h <= 0:
            return

        # clamp
        if self.cursor >= len(rows):
            self.cursor = len(rows) - 1
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        if self.cursor >= self.scroll + body_h:
            self.scroll = self.cursor - body_h + 1

        visible = rows[self.scroll : self.scroll + body_h]
        for i, r in enumerate(visible):
            name = str(r.get("cache_name") or r.get("name") or "?")
            size = _as_int(r.get("size"))
            mx = _as_int(r.get("maxsize") or r.get("max") or 0)
            hpm = _as_float(r.get("hits/min") or r.get("hits_per_min") or 0)
            ipm = _as_float(r.get("inserts/min") or r.get("inserts_per_min") or 0)
            hr = _as_float(str(r.get("hitrate") or "0").rstrip("%"))
            row_attr = curses.A_REVERSE if self.scroll + i == self.cursor else 0
            trend = store.cache_history.series(name, "hits_per_min", samples=20)
            trend_str = sparkline(trend, width=20) if trend else " " * 20
            cells = [
                (f"{name[:34]:<34} ", 0),
                (f"{human_count(size):>9} ", theme.fill_color(size, mx)),
                (f"{human_count(mx):>9} ", 0),
                (f"{hpm:>9.1f} ", 0),
                (f"{ipm:>9.1f} ", 0),
                (f"{hr:>5.1f}%  ", theme.hitrate_color(hr)),
                (hbar(hr, 100, 20), theme.hitrate_color(hr)),
                ("  ", 0),
                (trend_str, theme.attr(theme.P_SPARK)),
            ]
            write_row(win, body_top + i, 0, cells, row_attr=row_attr)
