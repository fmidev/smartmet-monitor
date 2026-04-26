"""Overview panel — global sparklines and totals."""

from __future__ import annotations

import curses

from .. import theme
from ..state.store import HISTORY_MINUTES
from ..widgets.bars import (
    downsample_avg, hbar, human_bytes, human_count, human_ms, sparkline, vchart,
)
from .base import Panel, safe_addstr, write_row


class OverviewPanel(Panel):
    name = "Overview"
    hotkey = "o"
    help_text = "Global sparklines: request rate, latency, bandwidth, errors."

    def export_snapshot(self, store):
        headers = ["window_min", "reqs", "mean_ms", "p50_ms", "p95_ms",
                   "max_ms", "total_bytes", "errors", "err_pct"]
        rows = []
        for m in (1, 5, 15, 60):
            b = store.global_window(m)
            rows.append([
                m, b.count,
                round(b.hist.mean(), 3),
                round(b.hist.p50(), 3),
                round(b.hist.p95(), 3),
                round(b.hist.max_ms, 3),
                b.bytes, b.errors,
                round(b.errors / b.count * 100, 3) if b.count else 0,
            ])
        return headers, rows

    def draw(self, win, store):
        h, w = win.getmaxyx()
        safe_addstr(win, 0, 0, " Overview — all URLs, last 60 min".ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

        # Last-60m merged stats
        win60 = store.global_window(60)
        win5 = store.global_window(5)
        win1 = store.global_window(1)

        row = 2
        safe_addstr(win, row, 2, "Totals", theme.attr(theme.P_HEADER, curses.A_BOLD))
        row += 1
        safe_addstr(win, row, 2,
                    f"{'win':>5}  {'reqs':>10} {'req/min':>9} {'mean_ms':>8} "
                    f"{'p95_ms':>8} {'MB_out':>10} {'err%':>6}",
                    theme.attr(theme.P_HEADER))
        row += 1
        for label, b in (("1m", win1), ("5m", win5), ("60m", win60)):
            if b.count == 0:
                safe_addstr(win, row, 2, f"{label:>5}  (no data yet)",
                            theme.attr(theme.P_DIM))
                row += 1
                continue
            mins = max(1, int(label[:-1]))
            rpm = b.count / mins
            err_pct = b.errors / b.count * 100
            cells = [
                (f"{label:>5}  ", 0),
                (f"{human_count(b.count):>10} ", 0),
                (f"{rpm:>9.1f} ", 0),
                (f"{human_ms(b.hist.mean()):>8} ", theme.latency_color(b.hist.mean())),
                (f"{human_ms(b.hist.p95()):>8} ", theme.latency_color(b.hist.p95())),
                (f"{b.bytes / 1_048_576:>10.2f} ", 0),
                (f"{err_pct:>5.1f}%", theme.err_color(err_pct)),
            ]
            write_row(win, row, 2, cells)
            row += 1

        row += 1
        # Stack 4 charts vertically, each full width, spanning the full
        # retained history (--history-minutes, default 60). Vertical
        # stacking gives each chart enough horizontal real estate to
        # show meaningful per-minute resolution; the previous 4-up
        # side-by-side layout left every chart < 30 chars wide.
        label_w = 8  # fits "99999.9 " right-padded
        chart_w = max(20, w - label_w - 4)
        # Available rows for the chart region:
        #   - reserve the bottom 1 row for a time-axis label
        #   - allocate the rest equally among 4 charts
        chart_region = max(0, h - row - 2)
        chart_count = 4
        if chart_region < chart_count * 3:
            return  # too cramped to render anything useful
        per_chart = chart_region // chart_count
        chart_h = max(2, per_chart - 2)  # leave 1 title row + 1 spacer
        history = max(2, HISTORY_MINUTES)
        # The chart can show `chart_w + 1` samples (one per spark cell
        # plus the overlap point). Average-downsample the full retained
        # history into that many buckets so the WHOLE history is always
        # shown, compressed to fit the terminal width — instead of just
        # the last `chart_w + 1` minutes.
        target_samples = chart_w + 1
        charts = [
            ("req/min",   downsample_avg(
                store.global_series(history, "count"), target_samples),
             theme.P_SPARK),
            ("mean ms",   downsample_avg(
                store.global_series(history, "mean_ms"), target_samples),
             theme.P_WARN),
            ("MB/min",    downsample_avg(
                [v / 1_048_576
                 for v in store.global_series(history, "bytes")],
                target_samples),
             theme.P_GOOD),
            ("err %",     downsample_avg(
                store.global_series(history, "err_pct"), target_samples),
             theme.P_BAD),
        ]
        # Express the actual span as a friendly label.
        if history >= 1440:
            span_label = f"{history // 60}h" if history % 60 == 0 else f"{history}m"
        else:
            span_label = f"{history}m"
        for chart_idx, (title, series, color) in enumerate(charts):
            top = row + chart_idx * per_chart
            maxv = max(series) if series else 0
            # Title row carries the metric name + scale + history span.
            safe_addstr(win, top, 2,
                        f"{title}  max={maxv:.2f}  "
                        f"(last {span_label})".ljust(label_w + chart_w),
                        theme.attr(theme.P_HEADER, curses.A_BOLD))
            rows = vchart(series, chart_h, cell_width=1, maxval=maxv,
                          width=chart_w)
            mid_row = chart_h // 2
            for j, line in enumerate(rows):
                if j == 0:
                    label_val = maxv
                elif j == chart_h - 1:
                    label_val = 0.0
                elif j == mid_row:
                    label_val = maxv / 2
                else:
                    label_val = None
                if label_val is None:
                    label = " " * label_w
                else:
                    label = f"{label_val:>{label_w - 1}.1f} "
                safe_addstr(win, top + 1 + j, 2, label,
                            theme.attr(theme.P_DIM))
                safe_addstr(win, top + 1 + j, 2 + label_w, line,
                            theme.attr(color))
            # Time-axis labels under the very last chart only — older
            # charts share the same x-axis so labelling each is noisy.
            if chart_idx == chart_count - 1:
                axis_row = top + 1 + chart_h
                if axis_row < h - 1:
                    left_label = f"-{span_label}"
                    if history >= 1440 and history % 60 == 0:
                        mid_label = f"-{history // 120}h"
                    else:
                        mid_label = f"-{history // 2}m"
                    axis_left = 2 + label_w
                    safe_addstr(win, axis_row, axis_left, left_label,
                                theme.attr(theme.P_DIM))
                    if chart_w > 30:
                        safe_addstr(win, axis_row,
                                    axis_left + chart_w // 2 - len(mid_label) // 2,
                                    mid_label, theme.attr(theme.P_DIM))
                    safe_addstr(win, axis_row,
                                axis_left + chart_w - len("now"),
                                "now", theme.attr(theme.P_DIM))
