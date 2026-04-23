"""Overview panel — global sparklines and totals."""

from __future__ import annotations

import curses

from .. import theme
from ..widgets.bars import hbar, sparkline, human_bytes, human_count, human_ms, vchart
from .base import Panel, safe_addstr, write_row


class OverviewPanel(Panel):
    name = "Overview"
    hotkey = "1"
    help_text = "Global sparklines: request rate, latency, bandwidth, errors."

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
        cw = max(10, (w - 8) // 4)
        # Four vertical mini-charts side by side
        charts = [
            ("req/min",   store.global_series(60, "count"),               theme.P_SPARK),
            ("mean ms",   store.global_series(60, "mean_ms"),             theme.P_WARN),
            ("MB/min",    [v / 1_048_576 for v in store.global_series(60, "bytes")], theme.P_GOOD),
            ("err %",     store.global_series(60, "err_pct"),             theme.P_BAD),
        ]
        chart_h = min(10, h - row - 4)
        if chart_h <= 2:
            return
        col_x = 2
        for title, series, color in charts:
            maxv = max(series) if series else 0
            safe_addstr(win, row, col_x, f"{title}  max={maxv:.1f}",
                        theme.attr(theme.P_HEADER, curses.A_BOLD))
            rows = vchart(series, chart_h, cell_width=1, maxval=maxv)
            for j, line in enumerate(rows):
                safe_addstr(win, row + 1 + j, col_x, line, theme.attr(color))
            col_x += cw + 2
            if col_x + cw > w:
                break

        # sparkline of request rate across full width
        srow = row + chart_h + 2
        if srow < h - 2:
            safe_addstr(win, srow, 2, "requests/min (last 60m):",
                        theme.attr(theme.P_HEADER, curses.A_BOLD))
            series = store.global_series(60, "count")
            safe_addstr(win, srow + 1, 2, sparkline(series, width=min(60, w - 4)),
                        theme.attr(theme.P_SPARK))
