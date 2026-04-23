"""API Keys panel — per-key aggregate stats with URL drill-in."""

from __future__ import annotations

import curses
from typing import List, Optional, Tuple

from .. import theme
from ..state.store import MinuteBucket
from ..widgets.bars import hbar, sparkline, human_count, human_ms
from .base import Panel, safe_addstr, write_row

WINDOWS = (1, 5, 15, 60)

SORT_COLS = (
    ("reqs",   "count",    "request count"),
    ("p95",    "p95",      "95th percentile latency"),
    ("mean",   "mean_ms",  "mean latency"),
    ("MB",     "mb_tot",   "total bandwidth"),
    ("err",    "err_pct",  "error %"),
    ("key",    "key_asc",  "apikey a→z"),
)


class KeysPanel(Panel):
    name = "Keys"
    hotkey = "7"
    help_text = (
        "API keys. s/S sort, [/] resize window, / filter, "
        "Enter drills into a key to see top URLs."
    )

    def __init__(self):
        self.sort_idx = 0
        self.reverse = True
        self.window_idx = 3  # 60 minutes (keys are usually long-lived)
        self.cursor = 0
        self.scroll = 0
        self.filter = ""
        self.filter_editing = False
        self.detail_key: Optional[str] = None

    def handle_key(self, key, store):
        if self.filter_editing:
            return self._handle_filter_key(key)
        if self.detail_key is not None:
            return self._handle_detail_key(key)
        if key in (curses.KEY_UP, ord("k")):
            self.cursor = max(0, self.cursor - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.cursor += 1
        elif key == curses.KEY_PPAGE:
            self.cursor = max(0, self.cursor - 10)
        elif key == curses.KEY_NPAGE:
            self.cursor += 10
        elif key in (curses.KEY_HOME, ord("g")):
            self.cursor = 0; self.scroll = 0
        elif key in (curses.KEY_END, ord("G")):
            self.cursor = 10_000_000
        elif key == ord("s"):
            self.sort_idx = (self.sort_idx + 1) % len(SORT_COLS)
        elif key == ord("S"):
            self.sort_idx = (self.sort_idx - 1) % len(SORT_COLS)
        elif key == ord("r"):
            self.reverse = not self.reverse
        elif key == ord("["):
            self.window_idx = max(0, self.window_idx - 1)
        elif key == ord("]"):
            self.window_idx = min(len(WINDOWS) - 1, self.window_idx + 1)
        elif key == ord("/"):
            self.filter_editing = True
        elif key in (10, 13, curses.KEY_ENTER):
            rows = self._sorted(store)
            if rows and 0 <= self.cursor < len(rows):
                self.detail_key = rows[self.cursor][0]
        elif key == 27:
            self.filter = ""
        return None

    def _handle_filter_key(self, key):
        if key in (10, 13, curses.KEY_ENTER, 27):
            self.filter_editing = False
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.filter = self.filter[:-1]
        elif 32 <= key < 127:
            self.filter += chr(key)
        return None

    def _handle_detail_key(self, key):
        if key in (27, ord("q"), curses.KEY_LEFT, ord("h"), ord("b")):
            self.detail_key = None
        return None

    def export_snapshot(self, store):
        rows = self._sorted(store)
        win_min = WINDOWS[self.window_idx]
        headers = ["apikey", "window_min", "count", "mean_ms", "p50_ms",
                   "p95_ms", "max_ms", "total_bytes", "errors", "err_pct"]
        out = []
        for k, b in rows:
            out.append([
                k, win_min, b.count,
                round(b.hist.mean(), 3),
                round(b.hist.p50(), 3),
                round(b.hist.p95(), 3),
                round(b.hist.max_ms, 3),
                b.bytes, b.errors,
                round(b.errors / b.count * 100, 3) if b.count else 0,
            ])
        return headers, out

    def _sorted(self, store) -> List[Tuple[str, MinuteBucket]]:
        win_min = WINDOWS[self.window_idx]
        rows = store.snapshot_keys(win_min)
        if self.filter:
            f = self.filter.lower()
            rows = [(k, b) for (k, b) in rows if f in k.lower()]
        key_name = SORT_COLS[self.sort_idx][1]

        def keyfn(item):
            k, b = item
            if key_name == "count": return b.count
            if key_name == "p95": return b.hist.p95()
            if key_name == "mean_ms": return b.hist.mean()
            if key_name == "mb_tot": return b.bytes
            if key_name == "err_pct":
                return (b.errors / b.count * 100) if b.count else 0
            if key_name == "key_asc": return k
            return 0

        rev = self.reverse
        if key_name == "key_asc":
            rev = False
        rows.sort(key=keyfn, reverse=rev)
        return rows

    def draw(self, win, store):
        if self.detail_key is not None:
            self._draw_detail(win, store)
            return
        h, w = win.getmaxyx()
        rows = self._sorted(store)
        win_min = WINDOWS[self.window_idx]
        sort_name = SORT_COLS[self.sort_idx][0]
        hdr = (f" window:{win_min}m  sort:{sort_name}{'↓' if self.reverse else '↑'}"
               f"  keys:{len(rows)}  filter:{self.filter or '<none>'}")
        safe_addstr(win, 0, 0, hdr.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))
        safe_addstr(win, 2, 0,
                    f"{'apikey':<44}  {'reqs':>7} {'mean':>7} {'p95':>7} "
                    f"{'MB':>8} {'err%':>5}  {'req trend':<20}",
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        safe_addstr(win, 3, 0, "─" * (w - 1), theme.attr(theme.P_DIM))

        body_top = 4
        body_h = h - body_top - 1
        if body_h <= 0:
            return
        if rows:
            if self.cursor >= len(rows):
                self.cursor = len(rows) - 1
            if self.cursor < self.scroll:
                self.scroll = self.cursor
            if self.cursor >= self.scroll + body_h:
                self.scroll = self.cursor - body_h + 1
        else:
            self.cursor = 0
            self.scroll = 0

        # per-row sparkline: request rate over last 30 minutes
        visible = rows[self.scroll : self.scroll + body_h]
        series_cache = {}
        max_trend = 0.0
        for k, _ in visible:
            ks = store.key_detail(k)
            if ks is None:
                continue
            s = ks.minute_series(30, "count")
            series_cache[k] = s
            m = max(s) if s else 0
            if m > max_trend:
                max_trend = m

        for i, (k, b) in enumerate(visible):
            count = b.count
            mean_ms = b.hist.mean()
            p95 = b.hist.p95()
            tot_mb = b.bytes / 1_048_576
            err_pct = (b.errors / count * 100) if count else 0
            row_attr = curses.A_REVERSE if self.scroll + i == self.cursor else 0
            trend = series_cache.get(k, [])
            trend_str = sparkline(trend, maxval=max_trend, width=20) if trend else " " * 20
            cells = [
                (f"{k[:44]:<44}  ", theme.attr(theme.P_ACCENT) if k != "-" else theme.attr(theme.P_DIM)),
                (f"{human_count(count):>7} ", 0),
                (f"{human_ms(mean_ms):>7} ", theme.latency_color(mean_ms)),
                (f"{human_ms(p95):>7} ", theme.latency_color(p95)),
                (f"{tot_mb:>8.2f} ", 0),
                (f"{err_pct:>4.1f}%  ", theme.err_color(err_pct)),
                (trend_str, theme.attr(theme.P_SPARK)),
            ]
            write_row(win, body_top + i, 0, cells, row_attr=row_attr)

    def _draw_detail(self, win, store):
        h, w = win.getmaxyx()
        k = self.detail_key
        ks = store.key_detail(k)
        safe_addstr(win, 0, 0, f" detail: {k}".ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))
        if ks is None:
            safe_addstr(win, 2, 2, "no data for this key")
            return

        row = 2
        safe_addstr(win, row, 2, "Windowed stats",
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        row += 1
        safe_addstr(win, row, 2,
                    f"{'window':>8}  {'reqs':>8} {'mean':>7} {'p50':>7} "
                    f"{'p95':>7} {'max':>7}  {'MB':>8} {'err%':>5}",
                    theme.attr(theme.P_HEADER))
        row += 1
        safe_addstr(win, row, 2, "─" * min(w - 4, 80), theme.attr(theme.P_DIM))
        row += 1
        for win_min in WINDOWS:
            b = ks.window(win_min)
            if b.count == 0:
                continue
            err_pct = b.errors / b.count * 100
            cells = [
                (f"{str(win_min) + 'm':>8}  ", 0),
                (f"{human_count(b.count):>8} ", 0),
                (f"{human_ms(b.hist.mean()):>7} ", theme.latency_color(b.hist.mean())),
                (f"{human_ms(b.hist.p50()):>7} ", 0),
                (f"{human_ms(b.hist.p95()):>7} ", theme.latency_color(b.hist.p95())),
                (f"{human_ms(b.hist.max_ms):>7}  ", 0),
                (f"{b.bytes / 1_048_576:>8.2f} ", 0),
                (f"{err_pct:>4.1f}%", theme.err_color(err_pct)),
            ]
            write_row(win, row, 2, cells)
            row += 1

        row += 1
        safe_addstr(win, row, 2, "Request rate per minute (last 60 min)",
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        row += 1
        series = ks.minute_series(60, "count")
        safe_addstr(win, row, 2, sparkline(series, width=60),
                    theme.attr(theme.P_SPARK))
        row += 1
        safe_addstr(win, row, 2, f"{'-60m':<20}{'-30m':<20}{'now':<20}",
                    theme.attr(theme.P_DIM))
        row += 2

        safe_addstr(win, row, 2, "Top URLs used by this key",
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        row += 1
        items = sorted(ks.apikey_counts.items(), key=lambda x: -x[1])[:12]
        mx = max((c for _, c in items), default=1)
        for url, c in items:
            bar = hbar(c, mx, 30)
            cells = [
                (f"{url[:50]:<50}  ", 0),
                (f"{human_count(c):>8}  ", 0),
                (bar, theme.attr(theme.P_SPARK)),
            ]
            write_row(win, row, 2, cells)
            row += 1
            if row >= h - 2:
                break

        safe_addstr(win, h - 1, 0, " [esc/left/q: back] ".ljust(w - 1),
                    theme.attr(theme.P_TITLE))
