"""URLs panel — primary view. Latency per URL, sortable, scrollable,
drill-in for detailed histogram + status + API keys."""

from __future__ import annotations

import curses
import time
from typing import List, Optional, Tuple

from .. import theme
from ..state.store import MinuteBucket
from ..widgets.bars import (
    hbar,
    sparkline,
    human_bytes,
    human_count,
    human_ms,
)
from .base import Panel, safe_addstr, write_row

# window sizes (minutes) the user can cycle through with [ / ]
WINDOWS = (1, 5, 15, 60)

# sortable columns
SORT_COLS = (
    ("reqs",   "reqs",    "count desc"),
    ("p95",    "p95",     "95th percentile latency desc"),
    ("p50",    "p50",     "50th percentile latency desc"),
    ("mean",   "mean_ms", "mean latency desc"),
    ("max",    "max_ms",  "max latency desc"),
    ("size",   "avg_kb",  "mean response size desc"),
    ("band",   "mb_tot",  "total bandwidth desc"),
    ("err",    "err_pct", "error % desc"),
    ("url",    "url_asc", "URL ascending"),
)


class UrlsPanel(Panel):
    name = "URLs"
    hotkey = "2"
    help_text = (
        "Latency per URL. Sort: s cycles, [/] resize window, "
        "Enter drills in, / filters, ↑↓ PgUp PgDn navigate."
    )

    def __init__(self) -> None:
        self.sort_idx = 1  # p95
        self.reverse = True
        self.window_idx = 1  # 5 minutes
        self.cursor = 0
        self.scroll = 0
        self.filter = ""
        self.filter_editing = False
        self.detail_url: Optional[str] = None

    # ---- key handling ------------------------------------------------------

    def handle_key(self, key, store):
        if self.filter_editing:
            return self._handle_filter_key(key)

        if self.detail_url is not None:
            return self._handle_detail_key(key)

        if key in (curses.KEY_UP, ord("k")):
            self.cursor = max(0, self.cursor - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.cursor += 1  # clamped in draw
        elif key == curses.KEY_PPAGE:
            self.cursor = max(0, self.cursor - 10)
        elif key == curses.KEY_NPAGE:
            self.cursor += 10
        elif key == curses.KEY_HOME or key == ord("g"):
            self.cursor = 0
            self.scroll = 0
        elif key == curses.KEY_END or key == ord("G"):
            self.cursor = 10_000_000  # clamped in draw
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
            urls = self._sorted_urls(store)
            if urls and 0 <= self.cursor < len(urls):
                self.detail_url = urls[self.cursor][0]
        elif key == 27:  # ESC clears filter
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
            self.detail_url = None
        return None

    # ---- selection / sort --------------------------------------------------

    def _sorted_urls(self, store) -> List[Tuple[str, MinuteBucket]]:
        win_min = WINDOWS[self.window_idx]
        urls = store.snapshot_urls(win_min)
        if self.filter:
            f = self.filter.lower()
            urls = [(u, b) for (u, b) in urls if f in u.lower()]
        key_name = SORT_COLS[self.sort_idx][1]

        def keyfn(item):
            url, b = item
            if key_name == "count":
                return b.count
            if key_name == "p95":
                return b.hist.p95()
            if key_name == "p50":
                return b.hist.p50()
            if key_name == "mean_ms":
                return b.hist.mean()
            if key_name == "max_ms":
                return b.hist.max_ms
            if key_name == "avg_kb":
                return (b.bytes / b.count / 1024) if b.count else 0
            if key_name == "mb_tot":
                return b.bytes
            if key_name == "err_pct":
                return (b.errors / b.count * 100) if b.count else 0
            if key_name == "url_asc":
                return url
            return 0

        rev = self.reverse
        if key_name == "url_asc":
            rev = False
        urls.sort(key=keyfn, reverse=rev)
        return urls

    # ---- drawing -----------------------------------------------------------

    def draw(self, win, store):
        if self.detail_url is not None:
            self._draw_detail(win, store)
            return

        h, w = win.getmaxyx()
        urls = self._sorted_urls(store)

        # header bar
        win_min = WINDOWS[self.window_idx]
        sort_name = SORT_COLS[self.sort_idx][0]
        hdr = (
            f" window:{win_min}m  sort:{sort_name}{'↓' if self.reverse else '↑'}"
            f"  urls:{len(urls)}  filter:{self.filter or '<none>'}"
            f"  [s/S:sort r:reverse [/]:window /:filter enter:drill"
        )
        safe_addstr(win, 0, 0, hdr.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

        # table header
        cols_text = (
            f"{'URL':<40}  {'reqs':>7} {'mean':>7} {'p50':>7} {'p95':>7} "
            f"{'max':>7}  {'KB':>6} {'MB':>7} {'err%':>5}  {'latency ' + str(win_min) + 'm':<30}"
        )
        safe_addstr(win, 2, 0, cols_text,
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        safe_addstr(win, 3, 0, "─" * (w - 1), theme.attr(theme.P_DIM))

        body_top = 4
        body_h = h - body_top - 1
        if body_h <= 0:
            return

        # clamp cursor and scroll
        if urls:
            if self.cursor >= len(urls):
                self.cursor = len(urls) - 1
            if self.cursor < self.scroll:
                self.scroll = self.cursor
            if self.cursor >= self.scroll + body_h:
                self.scroll = self.cursor - body_h + 1
        else:
            self.cursor = 0
            self.scroll = 0

        # find max for sparkline auto-scale (per-panel, not per-row)
        series_cache: dict = {}
        spark_max = 0.0
        visible = urls[self.scroll : self.scroll + body_h]
        for url, _ in visible:
            u = store.url_detail(url)
            if u is None:
                continue
            series = u.minute_series(min(win_min, 30), "mean_ms")
            series_cache[url] = series
            m = max(series) if series else 0
            if m > spark_max:
                spark_max = m

        for i, (url, b) in enumerate(visible):
            row = body_top + i
            count = b.count
            mean_ms = b.hist.mean()
            p50 = b.hist.p50()
            p95 = b.hist.p95()
            max_ms = b.hist.max_ms
            avg_kb = (b.bytes / count / 1024) if count else 0
            tot_mb = b.bytes / 1_048_576
            err_pct = (b.errors / count * 100) if count else 0

            row_attr = curses.A_REVERSE if self.scroll + i == self.cursor else 0
            cells = [
                (f"{url[:40]:<40}  ", 0),
                (f"{human_count(count):>7} ", 0),
                (f"{human_ms(mean_ms):>7} ", theme.latency_color(mean_ms)),
                (f"{human_ms(p50):>7} ", theme.latency_color(p50)),
                (f"{human_ms(p95):>7} ", theme.latency_color(p95)),
                (f"{human_ms(max_ms):>7}  ", theme.latency_color(max_ms)),
                (f"{avg_kb:>6.1f} ", 0),
                (f"{tot_mb:>7.2f} ", 0),
                (f"{err_pct:>4.1f}%  ", theme.err_color(err_pct)),
            ]
            x_end = write_row(win, row, 0, cells, row_attr=row_attr)

            avail_spark = max(0, w - x_end - 2)
            sl = sparkline(series_cache.get(url, []),
                           maxval=spark_max,
                           width=min(30, avail_spark))
            if sl:
                safe_addstr(win, row, x_end, sl,
                            theme.attr(theme.P_SPARK) | row_attr)

    def _draw_detail(self, win, store):
        h, w = win.getmaxyx()
        url = self.detail_url
        u = store.url_detail(url)
        safe_addstr(win, 0, 0, f" detail: {url}".ljust(w - 1), curses.A_REVERSE)

        if u is None:
            safe_addstr(win, 2, 2, "no data for this URL (yet)")
            safe_addstr(win, h - 1, 0, " [esc/left/q: back] ".ljust(w - 1),
                        curses.A_REVERSE)
            return

        # Windowed stats block
        row = 2
        safe_addstr(win, row, 2, "Windowed stats", curses.A_BOLD)
        row += 1
        safe_addstr(win, row, 2,
                    f"{'window':>8}  {'reqs':>8} {'mean':>7} {'p50':>7} "
                    f"{'p95':>7} {'p99':>7} {'max':>7}  {'avg_KB':>7} "
                    f"{'MB':>8} {'err%':>5}")
        row += 1
        safe_addstr(win, row, 2, "─" * min(w - 4, 90))
        row += 1
        for win_min in WINDOWS:
            b = u.window(win_min)
            if b.count == 0:
                continue
            safe_addstr(
                win, row, 2,
                f"{str(win_min) + 'm':>8}  "
                f"{human_count(b.count):>8} "
                f"{human_ms(b.hist.mean()):>7} "
                f"{human_ms(b.hist.p50()):>7} "
                f"{human_ms(b.hist.p95()):>7} "
                f"{human_ms(b.hist.p99()):>7} "
                f"{human_ms(b.hist.max_ms):>7}  "
                f"{(b.bytes / b.count / 1024):>7.1f} "
                f"{b.bytes / 1_048_576:>8.2f} "
                f"{(b.errors / b.count * 100):>4.1f}%",
            )
            row += 1

        # Latency sparkline across last 60 minutes
        row += 1
        safe_addstr(win, row, 2, "Mean latency per minute (last 60 min)", curses.A_BOLD)
        row += 1
        series = u.minute_series(60, "mean_ms")
        s = sparkline(series, width=60)
        safe_addstr(win, row, 2, s)
        row += 1
        # tick marks
        safe_addstr(win, row, 2, f"{'-60m':<20}{'-30m':<20}{'now':<20}")
        row += 2

        # Histogram bars (use the 5-min window to focus on recent)
        b5 = u.window(5)
        if b5.count > 0:
            safe_addstr(win, row, 2, "Latency distribution (last 5 min)", curses.A_BOLD)
            row += 1
            max_bin = max(b5.hist.buckets) if b5.hist.buckets else 0
            if max_bin > 0:
                for i, c in enumerate(b5.hist.buckets):
                    if c == 0 and i > 0 and all(b5.hist.buckets[j] == 0 for j in range(i)):
                        continue
                    if c == 0 and all(b5.hist.buckets[j] == 0 for j in range(i + 1, len(b5.hist.buckets))):
                        break
                    lo = 1.5 ** i if i > 0 else 0
                    hi = 1.5 ** (i + 1)
                    label = f"{lo:>7.1f}–{hi:>7.1f}ms "
                    bar = hbar(c, max_bin, 40)
                    safe_addstr(win, row, 4, f"{label}{bar} {c}")
                    row += 1
                    if row >= h - 6:
                        break

        # top 5 status codes
        row += 1
        if row < h - 3:
            safe_addstr(win, row, 2, "Status codes (last 5 min)", curses.A_BOLD)
            row += 1
            if b5.count > 0:
                items = sorted(b5.status_counts.items(), key=lambda x: -x[1])
                for st, c in items[:8]:
                    pct = c / b5.count * 100
                    safe_addstr(win, row, 4,
                                f"{st:>4}  {c:>6}  {pct:>5.1f}%  {hbar(c, b5.count, 30)}")
                    row += 1
                    if row >= h - 3:
                        break

        # top API keys (by count, all-time for this URL)
        if row < h - 3:
            safe_addstr(win, row, 2, "Top API keys (all time)", curses.A_BOLD)
            row += 1
            items = sorted(u.apikey_counts.items(), key=lambda x: -x[1])[:5]
            total = sum(c for _, c in items) or 1
            for k, c in items:
                safe_addstr(win, row, 4,
                            f"{k[:40]:<40}  {c:>8}  {hbar(c, total, 30)}")
                row += 1
                if row >= h - 2:
                    break

        safe_addstr(win, h - 1, 0, " [esc/left/q: back] ".ljust(w - 1),
                    curses.A_REVERSE)
