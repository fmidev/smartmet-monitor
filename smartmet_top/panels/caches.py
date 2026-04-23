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

    def export_snapshot(self, store):
        headers = ["host", "cache_name", "size", "maxsize",
                   "hits_per_min", "inserts_per_min", "hitrate_pct"]
        rows = []
        for host in store.admin_hosts:
            snap = store.cachestats.get(host)
            if snap is None or not snap.ok:
                continue
            for r in snap.rows or []:
                rows.append([
                    host,
                    str(r.get("cache_name") or r.get("name") or "?"),
                    _as_int(r.get("size")),
                    _as_int(r.get("maxsize") or r.get("max") or 0),
                    _as_float(r.get("hits/min") or r.get("hits_per_min")),
                    _as_float(r.get("inserts/min") or r.get("inserts_per_min")),
                    _as_float(str(r.get("hitrate") or "0").rstrip("%")),
                ])
        return headers, rows

    def draw(self, win, store):
        h, w = win.getmaxyx()
        hosts = store.admin_hosts
        if not hosts:
            safe_addstr(win, 0, 0, " Caches — no admin URLs configured".ljust(w - 1),
                        theme.attr(theme.P_DIM))
            return

        # aggregate flat row list: (host, row_dict)
        flat: list = []
        ok_count = 0
        err_msg = None
        for host in hosts:
            snap = store.cachestats.get(host)
            if snap is None:
                continue
            if snap.ok:
                ok_count += 1
                for r in snap.rows or []:
                    flat.append((host, r))
            elif err_msg is None:
                err_msg = f"{host}: {snap.error}"

        multi = len(hosts) > 1
        hdr_state = f"{ok_count}/{len(hosts)} hosts OK" if multi else (
            "OK" if ok_count == len(hosts) else "ERROR"
        )
        hdr_attr = (theme.attr(theme.P_TAB_ACTIVE) if ok_count == len(hosts)
                    else theme.attr(theme.P_BAD, curses.A_BOLD))
        safe_addstr(win, 0, 0, f" Caches — {hdr_state}".ljust(w - 1), hdr_attr)

        if err_msg and ok_count == 0:
            safe_addstr(win, 2, 2, f"error: {err_msg}", theme.attr(theme.P_BAD))
            return
        if not flat:
            safe_addstr(win, 2, 2, "no cache data available yet", theme.attr(theme.P_DIM))
            return

        # header
        host_col = 18 if multi else 0
        hdr_line = (
            (f"{'host':<{host_col}} " if multi else "")
            + f"{'cache':<34} {'size':>9} {'max':>9} {'hits/m':>9} {'ins/m':>9} "
            f"{'hit%':>6}  {'hitrate':<20}  {'hit/m trend':<20}"
        )
        safe_addstr(win, 2, 0, hdr_line, theme.attr(theme.P_HEADER, curses.A_BOLD))
        safe_addstr(win, 3, 0, "─" * (w - 1), theme.attr(theme.P_DIM))

        body_top = 4
        body_h = h - body_top - 1
        if body_h <= 0:
            return

        if self.cursor >= len(flat):
            self.cursor = len(flat) - 1
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        if self.cursor >= self.scroll + body_h:
            self.scroll = self.cursor - body_h + 1

        visible = flat[self.scroll : self.scroll + body_h]
        for i, (host, r) in enumerate(visible):
            name = str(r.get("cache_name") or r.get("name") or "?")
            size = _as_int(r.get("size"))
            mx = _as_int(r.get("maxsize") or r.get("max") or 0)
            hpm = _as_float(r.get("hits/min") or r.get("hits_per_min") or 0)
            ipm = _as_float(r.get("inserts/min") or r.get("inserts_per_min") or 0)
            hr = _as_float(str(r.get("hitrate") or "0").rstrip("%"))
            row_attr = curses.A_REVERSE if self.scroll + i == self.cursor else 0
            hist = store.cache_history.get(host)
            trend = hist.series(name, "hits_per_min", samples=20) if hist else []
            trend_str = sparkline(trend, width=20) if trend else " " * 20
            cells = []
            if multi:
                cells.append((f"{host[:host_col-1]:<{host_col}} ",
                              theme.attr(theme.P_ACCENT)))
            cells += [
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
