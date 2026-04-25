"""Live per-plugin access-log monitor.

One row per `*-access-log` file, each carrying the live request rate and
two independently auto-scaling Braille sparklines: response time and
response size. Per-second resolution over the last 60 s.

Each metric column auto-scales to the visible-row max so a low-traffic
plugin (`wfs-fin`, `default-handler`) is still readable next to a
high-traffic one (`wms`, `timeseries`). Toggle the time spark between
mean and p95 with `m`; toggle the size spark between mean response
size and bytes/sec throughput with `b`.
"""

from __future__ import annotations

import curses
from typing import List, Optional, Tuple

from .. import theme
from ..state.store import SourceStats
from ..widgets.bars import human_bytes, human_count, human_ms, sparkline
from .base import Panel, safe_addstr, write_row


# Sortable columns: (display_name, key, description)
SORT_COLS = (
    ("name",   "name",      "plugin name a→z"),
    ("req/s",  "rps",       "requests per second"),
    ("mean",   "mean_ms",   "mean response time"),
    ("p95",    "p95_ms",    "95th percentile response time"),
    ("err",    "err_pct",   "error %"),
    ("MB/s",   "bytes",     "bytes per second"),
)

# Window options: (display label, span, resolution).
# `span` is in seconds for "second"-resolution windows, in minutes for
# "minute"-resolution. Per-second mode shows live monitoring at 1Hz —
# `--replay` can't backfill it, anything older than 60s is gone — so
# the minute-resolution rows below let the operator zoom out and see
# the historical context that --replay does populate.
WINDOWS = (
    ("60s", 60,  "second"),
    ("1m",  1,   "minute"),
    ("5m",  5,   "minute"),
    ("15m", 15,  "minute"),
    ("60m", 60,  "minute"),
)


class PluginsPanel(Panel):
    name = "Graphs"
    hotkey = "g"
    help_text = (
        "Live per-plugin access-log monitor: req/s, mean/p95 latency, "
        "error % and two Braille sparklines (response time + response "
        "size, last 60s @ 1Hz). m toggles time mean↔p95, b toggles "
        "size mean↔throughput, s cycles sort, r reverses sort."
    )

    def __init__(self) -> None:
        self.cursor = 0
        self.scroll = 0
        # Spark metric toggles
        self.time_metric = "mean_ms"   # ↔ "p95_ms"
        self.size_metric = "bytes_mean"  # ↔ "bytes" (per-second throughput)
        # Sort state
        self.sort_idx = 1  # default: sort by req/s
        self.reverse = True
        # Optional name filter
        self.filter = ""
        self.filter_editing = False
        # Hide entirely-idle rows by default — production hosts have
        # many always-empty handler logs we don't want crowding the view.
        self.hide_idle = True
        # Window selector: 60s (live), 1m, 5m, 15m, 60m. Default 60s
        # keeps the live feel; `[` / `]` cycle, and the minute modes
        # are the ones --replay populates.
        self.window_idx = 0

    # ---- key handling ------------------------------------------------------

    def handle_key(self, key, store):
        if self.filter_editing:
            if key in (10, 13, curses.KEY_ENTER, 27):
                self.filter_editing = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.filter = self.filter[:-1]
            elif 32 <= key < 127:
                self.filter += chr(key)
            return True

        if key in (curses.KEY_UP, ord("k")):
            self.cursor = max(0, self.cursor - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.cursor += 1
        elif key == curses.KEY_PPAGE:
            self.cursor = max(0, self.cursor - 10)
        elif key == curses.KEY_NPAGE:
            self.cursor += 10
        elif key in (curses.KEY_HOME, ord("g")):
            # NOTE: `g` is also this panel's mnemonic, but delegate-first
            # routing means in-panel `g` lands here as "go to top".
            self.cursor = 0; self.scroll = 0
        elif key in (curses.KEY_END, ord("G")):
            self.cursor = 10_000_000
        elif key == ord("m"):
            self.time_metric = (
                "p95_ms" if self.time_metric == "mean_ms" else "mean_ms"
            )
        elif key == ord("b"):
            self.size_metric = (
                "bytes" if self.size_metric == "bytes_mean" else "bytes_mean"
            )
        elif key == ord("i"):
            self.hide_idle = not self.hide_idle
        elif key == ord("/"):
            self.filter_editing = True
        elif key == 27:
            self.filter = ""
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
        else:
            return False
        return True

    # ---- export ------------------------------------------------------------

    def export_snapshot(self, store):
        headers = [
            "plugin", "rps_60s", "mean_ms_60s", "p50_ms_60s", "p95_ms_60s",
            "max_ms_60s", "bytes_per_sec_60s", "err_pct_60s",
            "requests_60s", "errors_60s",
        ]
        rows = []
        for src, snap in self._sorted_rows(store):
            rows.append([
                src.label,
                round(snap.count / 60.0, 3),
                round(snap.hist.mean(), 3),
                round(snap.hist.p50(), 3),
                round(snap.hist.p95(), 3),
                round(snap.hist.max_ms, 3),
                round(snap.bytes / 60.0, 1),
                round(snap.errors / snap.count * 100.0, 3) if snap.count else 0.0,
                snap.count, snap.errors,
            ])
        return headers, rows

    # ---- selection / sort --------------------------------------------------

    def _sorted_rows(self, store) -> List[Tuple[SourceStats, "object"]]:
        """Return [(source_stats, current-window-bucket), ...] sorted per
        the current sort column. The window is computed once here so
        downstream callers don't redo the merge per redraw."""
        _, span, resolution = WINDOWS[self.window_idx]
        rows: List[Tuple[SourceStats, object]] = []
        for src in store.snapshot_sources():
            if resolution == "second":
                snap = src.second_window(span)
            else:
                snap = src.minute_window(span)
            if self.hide_idle and snap.count == 0:
                continue
            if self.filter and self.filter.lower() not in src.label.lower():
                continue
            rows.append((src, snap))

        key_name = SORT_COLS[self.sort_idx][1]

        def keyfn(item):
            src, snap = item
            if key_name == "name":
                return src.label
            if key_name == "rps":
                return snap.count
            if key_name == "mean_ms":
                return snap.hist.mean()
            if key_name == "p95_ms":
                return snap.hist.p95()
            if key_name == "err_pct":
                return (snap.errors / snap.count * 100) if snap.count else 0.0
            if key_name == "bytes":
                return snap.bytes
            return 0

        rev = self.reverse
        if key_name == "name":
            rev = False
        rows.sort(key=keyfn, reverse=rev)
        return rows

    # ---- drawing -----------------------------------------------------------

    def draw(self, win, store):
        h, w = win.getmaxyx()
        all_sources = store.snapshot_sources()
        total = len(all_sources)
        rows = self._sorted_rows(store)

        time_label = "p95" if self.time_metric == "p95_ms" else "mean"
        size_label = "B/s" if self.size_metric == "bytes" else "size"
        idle_state = "hidden" if self.hide_idle else "shown"
        sort_name = SORT_COLS[self.sort_idx][0]
        win_label, _, win_res = WINDOWS[self.window_idx]
        hdr = (
            f" Graphs — {len(rows)}/{total} log files  "
            f"window:{win_label}({win_res})  "
            f"sort:{sort_name}{'↓' if self.reverse else '↑'}  "
            f"time={time_label}  size={size_label}  idle={idle_state}  "
            f"filter:{self.filter or '<none>'}"
        )
        safe_addstr(win, 0, 0, hdr.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

        # Layout: name | req/s | mean | p95 | err% | spark1 | spark2
        # Allocate spark widths from whatever's left after the columns.
        name_col = 16
        num_cols = 8
        # 5 number columns × 8 = 40, plus name 16, plus separators ~10 = 66
        remainder = max(20, w - 70)
        spark_w = max(8, remainder // 2)
        time_w = spark_w
        size_w = spark_w

        if w < 60:
            safe_addstr(win, 2, 2, "terminal too narrow", theme.attr(theme.P_DIM))
            self._draw_footer(win)
            return

        # Header row — sparks span the active window, auto-scaled
        # column-wide.
        time_hdr = f"resp-{time_label} ({win_label})"
        size_hdr = (f"resp-bytes/s ({win_label})" if self.size_metric == "bytes"
                    else f"resp-size ({win_label})")
        col_hdr = (
            f"{'plugin':<{name_col}} "
            f"{'req/s':>{num_cols}} "
            f"{'mean':>{num_cols}} "
            f"{'p95':>{num_cols}} "
            f"{'err%':>{num_cols}}  "
            f"{time_hdr:<{time_w}}  "
            f"{size_hdr:<{size_w}}"
        )
        safe_addstr(win, 2, 0, col_hdr,
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        safe_addstr(win, 3, 0, "─" * (w - 1), theme.attr(theme.P_DIM))

        body_top = 4
        body_h = h - body_top - 1
        if body_h <= 0:
            self._draw_footer(win)
            return

        if rows:
            if self.cursor >= len(rows):
                self.cursor = len(rows) - 1
            if self.cursor < self.scroll:
                self.scroll = self.cursor
            if self.cursor >= self.scroll + body_h:
                self.scroll = self.cursor - body_h + 1
        else:
            self.cursor = 0; self.scroll = 0

        visible = rows[self.scroll : self.scroll + body_h]

        # Compute per-column max across visible rows so each spark
        # auto-scales to its own data range, independent of the others.
        _, span, resolution = WINDOWS[self.window_idx]
        time_series_cache: List[List[float]] = []
        size_series_cache: List[List[float]] = []
        for src, _ in visible:
            if resolution == "second":
                time_series_cache.append(src.second_series(self.time_metric, span))
                size_series_cache.append(src.second_series(self.size_metric, span))
            else:
                time_series_cache.append(src.minute_series(self.time_metric, span))
                size_series_cache.append(src.minute_series(self.size_metric, span))
        time_max = max((max(s, default=0.0) for s in time_series_cache), default=0.0)
        size_max = max((max(s, default=0.0) for s in size_series_cache), default=0.0)

        # req/s normalisation: in second-mode, snap.count is over
        # `span` seconds; in minute-mode, over `span` minutes.
        rps_divisor = float(span) if resolution == "second" else float(span * 60)
        for i, (src, snap) in enumerate(visible):
            y = body_top + i
            row_attr = curses.A_REVERSE if (self.scroll + i == self.cursor) else 0
            req_per_s = snap.count / rps_divisor if rps_divisor else 0.0
            mean_ms = snap.hist.mean()
            p95_ms = snap.hist.p95()
            err_pct = (snap.errors / snap.count * 100) if snap.count else 0.0

            cells = [
                (f"{src.label[:name_col]:<{name_col}} ",
                 theme.attr(theme.P_ACCENT) if snap.count > 0
                 else theme.attr(theme.P_DIM)),
                (f"{req_per_s:>{num_cols}.1f} ", 0),
                (f"{human_ms(mean_ms):>{num_cols}} ",
                 theme.latency_color(mean_ms)),
                (f"{human_ms(p95_ms):>{num_cols}} ",
                 theme.latency_color(p95_ms)),
                (f"{err_pct:>{num_cols-1}.1f}%  ",
                 theme.err_color(err_pct)),
            ]
            x = write_row(win, y, 0, cells, row_attr=row_attr)

            time_spark = sparkline(
                time_series_cache[i], maxval=time_max, width=time_w
            )
            safe_addstr(win, y, x, time_spark,
                        theme.attr(theme.P_WARN) | row_attr)
            x += time_w + 2

            size_spark = sparkline(
                size_series_cache[i], maxval=size_max, width=size_w
            )
            safe_addstr(win, y, x, size_spark,
                        theme.attr(theme.P_GOOD) | row_attr)

        self._draw_footer(win)

    def _draw_footer(self, win) -> None:
        h, w = win.getmaxyx()
        if h < 2:
            return
        if self.filter_editing:
            safe_addstr(win, h - 1, 0,
                        f" /{self.filter}_ (enter/esc to stop)".ljust(w - 1),
                        theme.attr(theme.P_HIGHLIGHT))
            return
        msg = (
            " s/S sort  r reverse  [/] window  m time mean↔p95  "
            "b size mean↔B/s  i idle on/off  / filter  e/E export "
        )
        safe_addstr(win, h - 1, 0, msg.ljust(w - 1),
                    theme.attr(theme.P_TITLE))
