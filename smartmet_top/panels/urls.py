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
    hotkey = "u"
    help_text = (
        "Latency per URL. Sort: s cycles, [/] resize window, "
        "Enter drills in, / filters, ↑↓/PgUp/PgDn/Home/End navigate."
    )

    def __init__(self) -> None:
        self.sort_idx = 1  # p95
        self.reverse = True
        self.window_idx = 1  # 5 minutes
        # Set by _sorted_urls each draw — what the panel actually
        # rendered, which can differ from window_idx when the selected
        # window was empty and we auto-widened.
        self._effective_window_idx = self.window_idx
        self.cursor = 0
        self.scroll = 0
        self.filter = ""
        self.filter_editing = False
        self.detail_url: Optional[str] = None
        # visibility toggles inside the drill-in view
        self.detail_show_hist = True
        self.detail_show_status = True
        self.detail_show_keys = True
        self.detail_window_idx = 1  # 5 minutes for histogram/status section

    # ---- key handling ------------------------------------------------------

    def handle_key(self, key, store):
        if self.filter_editing:
            return self._handle_filter_key(key)

        if self.detail_url is not None:
            return self._handle_detail_key(key, store)

        if key == curses.KEY_UP:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_DOWN:
            self.cursor += 1  # clamped in draw
        elif key == curses.KEY_PPAGE:
            self.cursor = max(0, self.cursor - 10)
        elif key == curses.KEY_NPAGE:
            self.cursor += 10
        elif key == curses.KEY_HOME:
            self.cursor = 0
            self.scroll = 0
        elif key == curses.KEY_END:
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
        else:
            return False
        return True

    def _handle_filter_key(self, key):
        if key in (10, 13, curses.KEY_ENTER, 27):
            self.filter_editing = False
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.filter = self.filter[:-1]
        elif 32 <= key < 127:
            self.filter += chr(key)
        return True

    def _handle_detail_key(self, key, store=None):
        if key in (27, curses.KEY_LEFT):
            self.detail_url = None
            return True
        # step through URLs in current sort order
        if key == curses.KEY_DOWN:
            self._step_detail(+1, store)
        elif key == curses.KEY_UP:
            self._step_detail(-1, store)
        elif key == ord("["):
            self.detail_window_idx = max(0, self.detail_window_idx - 1)
        elif key == ord("]"):
            self.detail_window_idx = min(len(WINDOWS) - 1, self.detail_window_idx + 1)
        # `h` would conflict with the Health panel mnemonic; the
        # histogram / status / keys sections are now always visible.
        else:
            return False
        return True

    def _step_detail(self, delta: int, store) -> None:
        if store is None or self.detail_url is None:
            return
        rows = self._sorted_urls(store)
        if not rows:
            return
        urls = [u for u, _ in rows]
        try:
            idx = urls.index(self.detail_url)
        except ValueError:
            idx = 0
        new = max(0, min(len(urls) - 1, idx + delta))
        self.detail_url = urls[new]
        self.cursor = new

    # ---- export ------------------------------------------------------------

    def export_snapshot(self, store):
        rows = self._sorted_urls(store)
        win_min = WINDOWS[self.window_idx]
        headers = ["url", "window_min", "count", "mean_ms", "p50_ms", "p95_ms",
                   "p99_ms", "max_ms", "avg_bytes", "total_bytes",
                   "errors", "err_pct"]
        out = []
        for url, b in rows:
            out.append([
                url,
                win_min,
                b.count,
                round(b.hist.mean(), 3),
                round(b.hist.p50(), 3),
                round(b.hist.p95(), 3),
                round(b.hist.p99(), 3),
                round(b.hist.max_ms, 3),
                int(b.bytes / b.count) if b.count else 0,
                b.bytes,
                b.errors,
                round(b.errors / b.count * 100, 3) if b.count else 0,
            ])
        return headers, out

    # External hook for cross-panel drill-in (Plugins → URLs filtered).
    def set_filter(self, value: str) -> None:
        self.filter = value
        self.cursor = 0
        self.scroll = 0
        # Reset detail mode so the operator lands on the table.
        self.detail_url = None

    # ---- selection / sort --------------------------------------------------

    def _sorted_urls(self, store) -> List[Tuple[str, MinuteBucket]]:
        # Auto-widen: if the operator's selected window has no URL data
        # (common right after --replay since recent log activity may all
        # be older than e.g. 5 minutes), fall through to the next wider
        # window with data. Mirrors the Plugins panel behaviour.
        rows = self._collect_urls(store, self.window_idx)
        self._effective_window_idx = self.window_idx
        if not rows:
            for try_idx in range(self.window_idx + 1, len(WINDOWS)):
                widened = self._collect_urls(store, try_idx)
                if widened:
                    rows = widened
                    self._effective_window_idx = try_idx
                    break
        return rows

    def _collect_urls(self, store, window_idx: int) -> List[Tuple[str, MinuteBucket]]:
        win_min = WINDOWS[window_idx]
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

        # header bar — when auto-widened, surface both the user's
        # selection and the effective rendered window so nothing is
        # silently swapped.
        win_min = WINDOWS[self.window_idx]
        eff_win_min = WINDOWS[self._effective_window_idx]
        sort_name = SORT_COLS[self.sort_idx][0]
        if self._effective_window_idx != self.window_idx:
            window_str = f"window:{win_min}m→{eff_win_min}m(auto-widened)"
        else:
            window_str = f"window:{win_min}m"
        hdr = (
            f" {window_str}  sort:{sort_name}{'↓' if self.reverse else '↑'}"
            f"  urls:{len(urls)}  filter:{self.filter or '<none>'}"
            f"  [s/S:sort r:reverse [/]:window /:filter enter:drill"
        )
        safe_addstr(win, 0, 0, hdr.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

        # table header
        cols_text = (
            f"{'URL':<40}  {'reqs':>7} {'mean':>7} {'p50':>7} {'p95':>7} "
            f"{'max':>7}  {'KB':>6} {'MB':>7} {'err%':>5}  {'latency ' + str(eff_win_min) + 'm':<30}"
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
        # position within the current sort for "n/m" header
        sorted_rows = self._sorted_urls(store)
        urls_list = [x[0] for x in sorted_rows]
        try:
            pos = urls_list.index(url) + 1
        except ValueError:
            pos = 0
        total_rows = len(urls_list)
        detail_w = WINDOWS[self.detail_window_idx]

        head = f" detail [{pos}/{total_rows}]: {url}   hist-window:{detail_w}m "
        safe_addstr(win, 0, 0, head.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

        if u is None:
            safe_addstr(win, 2, 2, "no data for this URL (yet)",
                        theme.attr(theme.P_DIM))
            safe_addstr(win, h - 1, 0, " [↑↓: prev/next URL | Esc/← : back] ".ljust(w - 1),
                        theme.attr(theme.P_TITLE))
            return

        row = 2
        safe_addstr(win, row, 2, "Windowed stats",
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        row += 1
        safe_addstr(win, row, 2,
                    f"{'window':>8}  {'reqs':>8} {'mean':>7} {'p50':>7} "
                    f"{'p95':>7} {'p99':>7} {'max':>7}  {'avg_KB':>7} "
                    f"{'MB':>8} {'err%':>5}",
                    theme.attr(theme.P_HEADER))
        row += 1
        safe_addstr(win, row, 2, "─" * min(w - 4, 90), theme.attr(theme.P_DIM))
        row += 1
        for win_min in WINDOWS:
            b = u.window(win_min)
            if b.count == 0:
                continue
            err_pct = b.errors / b.count * 100 if b.count else 0
            cells = [
                (f"{str(win_min) + 'm':>8}  ", 0),
                (f"{human_count(b.count):>8} ", 0),
                (f"{human_ms(b.hist.mean()):>7} ", theme.latency_color(b.hist.mean())),
                (f"{human_ms(b.hist.p50()):>7} ", theme.latency_color(b.hist.p50())),
                (f"{human_ms(b.hist.p95()):>7} ", theme.latency_color(b.hist.p95())),
                (f"{human_ms(b.hist.p99()):>7} ", theme.latency_color(b.hist.p99())),
                (f"{human_ms(b.hist.max_ms):>7}  ", 0),
                (f"{(b.bytes / b.count / 1024):>7.1f} ", 0),
                (f"{b.bytes / 1_048_576:>8.2f} ", 0),
                (f"{err_pct:>4.1f}%", theme.err_color(err_pct)),
            ]
            write_row(win, row, 2, cells)
            row += 1

        row += 1
        safe_addstr(win, row, 2, "Mean latency per minute (last 60 min)",
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        row += 1
        series = u.minute_series(60, "mean_ms")
        safe_addstr(win, row, 2, sparkline(series, width=60),
                    theme.attr(theme.P_SPARK))
        row += 1
        safe_addstr(win, row, 2, f"{'-60m':<20}{'-30m':<20}{'now':<20}",
                    theme.attr(theme.P_DIM))
        row += 2

        bw = u.window(detail_w)
        if self.detail_show_hist and bw.count > 0 and row < h - 4:
            safe_addstr(win, row, 2,
                        f"Latency distribution (last {detail_w} min)",
                        theme.attr(theme.P_HEADER, curses.A_BOLD))
            row += 1
            max_bin = max(bw.hist.buckets) if bw.hist.buckets else 0
            if max_bin > 0:
                leading_zero = 0
                trailing_zero = len(bw.hist.buckets)
                for j, c in enumerate(bw.hist.buckets):
                    if c == 0 and leading_zero == j:
                        leading_zero = j + 1
                for j in range(len(bw.hist.buckets) - 1, -1, -1):
                    if bw.hist.buckets[j] != 0:
                        trailing_zero = j + 1
                        break
                else:
                    trailing_zero = 1
                for j in range(leading_zero, trailing_zero):
                    c = bw.hist.buckets[j]
                    lo = 1.5 ** j if j > 0 else 0
                    hi = 1.5 ** (j + 1)
                    label = f"{lo:>7.1f}–{hi:>7.1f}ms "
                    bar = hbar(c, max_bin, 40)
                    cells = [
                        (label, theme.attr(theme.P_DIM)),
                        (bar, theme.latency_color(lo)),
                        (f" {c}", 0),
                    ]
                    write_row(win, row, 4, cells)
                    row += 1
                    if row >= h - 3:
                        break

        if self.detail_show_status and bw.count > 0 and row < h - 4:
            row += 1
            safe_addstr(win, row, 2, f"Status codes (last {detail_w} min)",
                        theme.attr(theme.P_HEADER, curses.A_BOLD))
            row += 1
            items = sorted(bw.status_counts.items(), key=lambda x: -x[1])
            for st, c in items[:6]:
                pct = c / bw.count * 100
                col = theme.attr(theme.P_BAD, curses.A_BOLD) if st >= 500 else \
                      theme.attr(theme.P_WARN) if st >= 400 else \
                      theme.attr(theme.P_GOOD)
                cells = [
                    (f"{st:>4}", col),
                    (f"  {c:>6}  {pct:>5.1f}%  ", 0),
                    (hbar(c, bw.count, 30), col),
                ]
                write_row(win, row, 4, cells)
                row += 1
                if row >= h - 3:
                    break

        if self.detail_show_keys and row < h - 3:
            row += 1
            safe_addstr(win, row, 2, "Top API keys (all time)",
                        theme.attr(theme.P_HEADER, curses.A_BOLD))
            row += 1
            items = sorted(u.apikey_counts.items(), key=lambda x: -x[1])[:5]
            total = sum(c for _, c in items) or 1
            for k, c in items:
                cells = [
                    (f"{k[:40]:<40}  ",
                     theme.attr(theme.P_ACCENT) if k != "-" else theme.attr(theme.P_DIM)),
                    (f"{c:>8}  ", 0),
                    (hbar(c, total, 30), theme.attr(theme.P_SPARK)),
                ]
                write_row(win, row, 4, cells)
                row += 1
                if row >= h - 2:
                    break

        safe_addstr(win, h - 1, 0,
                    " ↑↓ prev/next | [ ] window | e export | Esc/← back ".ljust(w - 1),
                    theme.attr(theme.P_TITLE))
