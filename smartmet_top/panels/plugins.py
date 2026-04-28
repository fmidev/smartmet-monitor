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
import time
from typing import List, Optional, Tuple

from .. import theme
from ..snapshots import plugins as plugins_snap
from ..snapshots.plugins import PluginsSnapshot
# Import the module rather than the constants so live changes from
# `set_history_minutes()` are picked up. `from .store import X` would
# bind the panel's local name at import time and never update.
from ..state import store as _store
from ..state.store import SourceStats
from ..widgets.bars import (
    human_bytes, human_count, human_ms, sparkline, vchart,
)
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
    panel_help = """\
One row per tailed access-log source — i.e. one row per plugin
(`wms`, `timeseries`, `download`, `wfs`, …). All numbers come
directly from access-log lines smtop tails; the panel does NOT
poll the admin endpoint, so it works on hosts where you don't
want admin access enabled.

Columns:
  plugin    short label derived from the access-log filename
            (`wms-access-log` → `wms`).
  req/s    requests per second over the active time window.
  mean     mean latency in ms.
  p95      95th-percentile latency. The slow-side number.
  err%     share of responses with HTTP status ≥ 400.

Two Braille sparklines per row:
  resp-mean / resp-p95
            request latency over time. Press `m` to toggle
            between mean and p95. Per-row max scaling so a
            quiet plugin's shape is visible alongside a busy
            one — the visual height is *relative*, not
            absolute.
  resp-size / resp-B/s
            response payload. Press `b` to toggle between
            mean response size and throughput in bytes/sec.

Time window:
  [ shrinks the window (60 s → 5 m → 15 m → 60 m, then back).
  ] grows it. Each tick of the sparklines covers an
  interval that scales with the window. Smtop auto-widens
  past empty windows.

Drill-in:
  Enter on a row jumps to the URLs panel pre-filtered to
  that plugin's URLs. Press `i` to flip back from the
  drill-in.

Keys:
  ↑ ↓ PgUp PgDn Home End   navigate
  Enter                    drill into URLs filtered by plugin
  m                        toggle time spark mean ↔ p95
  b                        toggle size spark mean ↔ throughput
  i                        toggle visibility of idle handlers
  s / S / r                sort cycle / reverse / direction
  [ / ]                    shrink / grow time window
  e / E                    export as CSV / JSON
"""

    def __init__(self, default_window_idx: int = 0,
                 default_cursor: int = 0,
                 default_hide_idle: bool = True) -> None:
        # default_cursor=-1 suppresses the cursor highlight entirely;
        # used by the Live composite, which is display-only and where
        # an arbitrary "selected row" indicator would be misleading.
        self.cursor = default_cursor
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
        # Hide entirely-idle rows by default in the dedicated Graphs
        # panel so always-empty handler logs (favicon, default-handler,
        # ...) don't crowd the view. The Live composite passes False
        # so its embedded Plugins shows the full plugin list — the
        # operator sees at a glance which plugins exist on the host.
        self.hide_idle = default_hide_idle
        # Window selector: 60s (live), 1m, 5m, 15m, 60m. Caller can
        # override the initial window — the Live composite uses 5m so
        # it isn't empty right after --replay (the 60s window only
        # populates from live tail, not from replayed history).
        self.window_idx = max(0, min(len(WINDOWS) - 1, default_window_idx))
        # Set by _sorted_rows on each draw — what the panel actually
        # ended up rendering, which can differ from window_idx when the
        # selected window is empty and we auto-widened.
        self._effective_window_idx: int = self.window_idx

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

        if key == curses.KEY_UP:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_DOWN:
            self.cursor += 1
        elif key in (10, 13, curses.KEY_ENTER):
            # Drill into the URLs panel filtered by the selected
            # plugin's label, so the operator can see which URLs
            # under that plugin are slow / busy.
            rows = self._sorted_rows(store)
            if rows and 0 <= self.cursor < len(rows):
                plugin_label = rows[self.cursor][0].label
                store.pending_panel_switch = ("u", {"filter": plugin_label})
        elif key == curses.KEY_PPAGE:
            self.cursor = max(0, self.cursor - 10)
        elif key == curses.KEY_NPAGE:
            self.cursor += 10
        elif key == curses.KEY_HOME:
            self.cursor = 0; self.scroll = 0
        elif key == curses.KEY_END:
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
        return PluginsSnapshot.table(
            store,
            window_label=WINDOWS[self.window_idx][0],
            sort=SORT_COLS[self.sort_idx][1],
            reverse=self.reverse,
            filter_str=self.filter,
            hide_idle=self.hide_idle,
        )

    # ---- selection / sort --------------------------------------------------

    def _sorted_rows(self, store) -> List[Tuple[SourceStats, "object"]]:
        """Return [(source_stats, current-window-bucket), ...] sorted per
        the current sort column. Auto-widens the window when the
        operator's selection has no data — common right after --replay,
        because the 60s per-second window can only be filled from live
        tail. The header reflects which window we actually rendered.
        """
        label = WINDOWS[self.window_idx][0]
        rows, eff_label = plugins_snap.collect_with_autowiden(
            store,
            window_label=label,
            sort=SORT_COLS[self.sort_idx][1],
            reverse=self.reverse,
            filter_str=self.filter,
            hide_idle=self.hide_idle,
        )
        self._effective_window_idx = self.window_idx
        for i, (lbl, _, _) in enumerate(WINDOWS):
            if lbl == eff_label:
                self._effective_window_idx = i
                break
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
        eff_label, eff_span, eff_resolution = WINDOWS[self._effective_window_idx]
        # If we auto-widened, show both the user's selection and what's
        # actually rendered so nobody is left wondering why the spark
        # column doesn't match the window picker.
        if self._effective_window_idx != self.window_idx:
            window_str = f"window:{win_label}→{eff_label}(auto-widened)"
        else:
            window_str = f"window:{win_label}({win_res})"
        hdr = (
            f" Graphs — {len(rows)}/{total} log files  "
            f"{window_str}  "
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

        # The spark always shows the longest history available at the
        # active resolution — `spark_w + 1` samples — so a wide column
        # is filled instead of leaving 80% of the bar as zero-padding.
        # The column header advertises the actual span being drawn.
        if eff_resolution == "second":
            spark_samples = min(spark_w + 1, _store.HISTORY_SECONDS)
            spark_span_seconds = spark_samples - 1
            spark_span_label = f"{spark_span_seconds}s"
        else:
            spark_samples = min(spark_w + 1, _store.HISTORY_MINUTES)
            spark_span_seconds = (spark_samples - 1) * 60
            if spark_samples - 1 >= 60 and (spark_samples - 1) % 60 == 0:
                spark_span_label = f"{(spark_samples - 1) // 60}h"
            else:
                spark_span_label = f"{spark_samples - 1}m"
        time_hdr = f"resp-{time_label} ({spark_span_label})"
        size_hdr = (f"resp-bytes/s ({spark_span_label})" if self.size_metric == "bytes"
                    else f"resp-size ({spark_span_label})")
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
        # Reserve one row at the bottom of the body for a shared time
        # axis (HH:MM clock labels under the spark columns).
        axis_h = 1
        body_h = h - body_top - 1 - axis_h
        if body_h <= 0:
            self._draw_footer(win)
            return

        # Decide compact (1 row per plugin) vs tall (N rows per plugin)
        # based on how much vertical room each plugin gets. Tall mode
        # turns each plugin row into a multi-row vchart for both sparks
        # — each plugin's pattern is much more readable, but only when
        # there are few enough plugins that they fit. Cap tall height
        # at 8 rows per plugin so a small plugin list doesn't waste
        # the whole screen on one row.
        if rows:
            n_total = len(rows)
            max_per_plugin = max(1, body_h // n_total)
            per_plugin = min(max_per_plugin, 8) if max_per_plugin >= 3 else 1
        else:
            per_plugin = 1
        plugins_per_screen = max(1, body_h // per_plugin)

        if rows:
            if self.cursor >= 0:
                if self.cursor >= len(rows):
                    self.cursor = len(rows) - 1
                if self.cursor < self.scroll:
                    self.scroll = self.cursor
                if self.cursor >= self.scroll + plugins_per_screen:
                    self.scroll = self.cursor - plugins_per_screen + 1
            max_scroll = max(0, len(rows) - plugins_per_screen)
            self.scroll = max(0, min(self.scroll, max_scroll))
        else:
            self.scroll = 0
            if self.cursor >= 0:
                self.cursor = 0

        visible = rows[self.scroll : self.scroll + plugins_per_screen]

        time_series_cache: List[List[float]] = []
        size_series_cache: List[List[float]] = []
        for src, _ in visible:
            if eff_resolution == "second":
                time_series_cache.append(src.second_series(self.time_metric, spark_samples))
                size_series_cache.append(src.second_series(self.size_metric, spark_samples))
            else:
                time_series_cache.append(src.minute_series(self.time_metric, spark_samples))
                size_series_cache.append(src.minute_series(self.size_metric, spark_samples))

        rps_divisor = (float(eff_span) if eff_resolution == "second"
                       else float(eff_span * 60))
        for i, (src, snap) in enumerate(visible):
            y_top = body_top + i * per_plugin
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
            x = write_row(win, y_top, 0, cells, row_attr=row_attr)

            if per_plugin == 1:
                # Compact: one Braille sparkline per metric.
                time_spark = sparkline(
                    time_series_cache[i], maxval=0.0, width=time_w
                )
                safe_addstr(win, y_top, x, time_spark,
                            theme.attr(theme.P_WARN) | row_attr)
                x += time_w + 2
                size_spark = sparkline(
                    size_series_cache[i], maxval=0.0, width=size_w
                )
                safe_addstr(win, y_top, x, size_spark,
                            theme.attr(theme.P_GOOD) | row_attr)
            else:
                # Tall: vertical chart for each metric, occupying
                # all `per_plugin` rows of this plugin's block. The
                # numeric stats sit on the top row; the chart fills
                # the rest of the right side.
                time_lines = vchart(
                    time_series_cache[i], per_plugin, width=time_w,
                    maxval=0.0,
                )
                for j, line in enumerate(time_lines):
                    safe_addstr(win, y_top + j, x, line,
                                theme.attr(theme.P_WARN))
                size_lines = vchart(
                    size_series_cache[i], per_plugin, width=size_w,
                    maxval=0.0,
                )
                for j, line in enumerate(size_lines):
                    safe_addstr(win, y_top + j, x + time_w + 2, line,
                                theme.attr(theme.P_GOOD))

        # Shared time axis under the spark columns: HH:MM at the left
        # edge, midpoint and right edge of each spark, anchored to
        # local clock time so axis labels match the timestamps in the
        # underlying access logs.
        axis_row = body_top + body_h
        if axis_row < h - 1 and visible:
            now_ts = time.time()
            right_ts = now_ts
            left_ts = now_ts - spark_span_seconds
            mid_ts = now_ts - spark_span_seconds / 2
            # Earlier this was a ternary-of-lambdas, but `lambda x: A
            # if B else lambda x: C` parses as one lambda whose body
            # is a ternary returning either a string or a lambda —
            # so the second branch evaluated to a function and
            # subsequent len(label) blew up. Pick the format up front.
            if eff_resolution == "second" and spark_span_seconds < 60:
                fmt_str = "%H:%M:%S"
            else:
                fmt_str = "%H:%M"
            left_label = time.strftime(fmt_str, time.localtime(left_ts))
            mid_label = time.strftime(fmt_str, time.localtime(mid_ts))
            right_label = time.strftime(fmt_str, time.localtime(right_ts))
            # First spark column starts at the x where the rendering
            # loop placed it (after the header cells); we recover it
            # by reusing the same write_row positions on a dummy
            # cell row. Easier: take the last visible row's `x`
            # value (we stored it in the loop). For simplicity, we
            # recompute it from the column widths used above.
            x_chart = (name_col + 1
                       + (num_cols + 1) * 3
                       + (num_cols + 1) + 1)
            # Layout: time chart [x_chart .. x_chart+time_w]
            #         gap (2 cols)
            #         size chart [.. + size_w]
            attr_dim = theme.attr(theme.P_DIM)
            self._draw_axis_for_chart(win, axis_row, x_chart, time_w,
                                      left_label, mid_label, right_label,
                                      attr_dim)
            self._draw_axis_for_chart(win, axis_row,
                                      x_chart + time_w + 2, size_w,
                                      left_label, mid_label, right_label,
                                      attr_dim)

        self._draw_footer(win)

    def _draw_axis_for_chart(self, win, y: int, x: int, width: int,
                             left_label: str, mid_label: str,
                             right_label: str, attr: int) -> None:
        """Place left/mid/right HH:MM labels under one spark column."""
        if width < len(left_label) + len(right_label) + 1:
            return  # too narrow, skip
        safe_addstr(win, y, x, left_label, attr)
        if width >= 30:
            mid_x = x + width // 2 - len(mid_label) // 2
            safe_addstr(win, y, mid_x, mid_label, attr)
        safe_addstr(win, y, x + width - len(right_label),
                    right_label, attr)

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
