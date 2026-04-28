"""Caches panel — admin-plugin ?what=cachestats."""

from __future__ import annotations

import curses
import time

from .. import theme
from ..snapshots.caches import CachesSnapshot
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
    hotkey = "c"
    help_text = "Cache stats polled from /admin?what=cachestats."
    panel_help = """\
Per-cache statistics polled from `?what=cachestats` on the
admin endpoint. Each row is one named cache inside spine /
the engines (querydata, geonames, contour, etc).

Columns:
  name       cache identity (e.g. `querydata::content`,
             `contour::isobands`).
  size       current entries.
  max        maxsize — the cache's configured upper bound.
             A consistent `size == max` means the cache is
             saturated; its eviction policy is now in play.
  hits/min   hit-rate sample over the last minute.
  ins/min    insertion rate (cache misses that produced a
             new entry).
  hit%       cumulative hit rate since startup. Below 50%
             usually means the cache is too small or the
             working set is wider than the cache assumes.
  hitrate    horizontal bar from 0% to 100% plus a Braille
             sparkline of the recent trend.

What the numbers mean:
  - hit% near 100% with hits/min high  → cache is doing its
    job; nothing to fix.
  - hit% high but ins/min also high    → cache is churning;
    insertions are evicting hot entries. Either undersized or
    the request mix is wider than the cache assumes.
  - hit% low and ins/min low           → little traffic uses
    this cache; not necessarily a problem.
  - size pinned at max with low hit%   → resize the cache
    upward in spine config.

Keys:
  ↑ ↓ PgUp PgDn   scroll
  e / E           export as CSV / JSON
"""

    def __init__(self):
        self.cursor = 0
        self.scroll = 0

    def handle_key(self, key, store):
        if key == curses.KEY_UP:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_DOWN:
            self.cursor += 1
        elif key == curses.KEY_PPAGE:
            self.cursor = max(0, self.cursor - 10)
        elif key == curses.KEY_NPAGE:
            self.cursor += 10
        else:
            return False
        return True

    def export_snapshot(self, store):
        return CachesSnapshot.table(store)

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
        # Fixed left columns: host(opt) + cache(34+sp) + 4 numbers(9+sp each)
        # + hit%(5.1f+%+2sp) = (host_col+1) + 35 + 40 + 8.
        fixed_left = (host_col + 1 if multi else 0) + 35 + 40 + 8
        trend_w = 20
        bar_w = max(10, w - fixed_left - trend_w - 4)
        hdr_line = (
            (f"{'host':<{host_col}} " if multi else "")
            + f"{'cache':<34} {'size':>9} {'max':>9} {'hits/m':>9} {'ins/m':>9} "
            f"{'hit%':>6}  {'hitrate':<{bar_w}}  {'hit/m trend':<{trend_w}}"
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
            trend = hist.series(name, "hits_per_min",
                                samples=trend_w + 1) if hist else []
            trend_str = (sparkline(trend, width=trend_w) if trend
                         else " " * trend_w)
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
                (hbar(hr, 100, bar_w), theme.hitrate_color(hr)),
                ("  ", 0),
                (trend_str, theme.attr(theme.P_SPARK)),
            ]
            write_row(win, body_top + i, 0, cells, row_attr=row_attr)
