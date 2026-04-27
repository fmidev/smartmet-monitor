"""Services panel — admin-plugin ?what=servicestats."""

from __future__ import annotations

import curses
import time

from .. import theme
from ..widgets.bars import hbar, human_count, human_ms, sparkline, vchart
from .base import Panel, safe_addstr, write_row


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class ServicesPanel(Panel):
    name = "Services"
    hotkey = "s"
    help_text = "Per-handler throughput from /admin?what=servicestats."
    panel_help = """\
Per-handler throughput, polled from `?what=servicestats` on the
admin endpoint. One row per registered HTTP request handler.

Columns:
  handler   — fully-qualified handler path (e.g. /timeseries,
              /wms?service=WMS&request=GetMap, /admin?…).
              The same name spine uses to register the handler.
  last1m    — requests served in the last full minute. The
              fastest-moving column; what to watch during a
              traffic surge.
  last1h    — requests served in the last hour. Useful for
              "is this handler trending up?" without minute-
              level noise.
  last24h   — requests in the last day. Long-term baseline.
  avg_ms    — wall-clock mean duration over the rolling
              window (which window depends on spine version
              but is typically the last 1 h).
  cpu%      — fraction of avg_ms the handler spent ON CPU
              (the rest was off-CPU: lock waits, I/O,
              upstream calls). Computed from the
              AverageCPUMs field added in spine 26.4.27. The
              cell is coloured by ratio:
                green  ≥ 50%  CPU-bound; the on-CPU flame is
                              the next stop.
                blue   ≤ 10%  wait-bound; open the off-CPU
                              flame to see what it is
                              waiting on.
                neutral 10-50% mixed.
                "—"          spine on this host is older than
                              26.4.27 — upgrade to read this
                              column.
  trend     — Braille sparkline of req/min over the recent
              admin-poll history. Same width and sample
              cadence as the Caches panel.

Reading the panel:
  - Sort changes nothing about the data; it just decides which
    handlers fill the visible rows on a tall list.
  - A handler whose last1h is high but last1m is zero is in
    a quiet spot — could be the next surge candidate or could
    just be a low-priority background task.
  - last1m + high avg_ms together = saturation. Pair with the
    URLs panel: which URLs of this handler are slow?

Keys:
  ↑ ↓ PgUp PgDn Home End   navigate
  s / S    cycle sort column
  r        reverse sort
  e / E    export as CSV / JSON
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
        headers = ["host", "handler", "req_per_min", "req_per_hour",
                   "req_per_day", "avg_ms", "avg_cpu_ms"]
        rows = []
        for host in store.admin_hosts:
            snap = store.servicestats.get(host)
            if snap is None or not snap.ok:
                continue
            for r in snap.rows or []:
                rows.append([
                    host,
                    str(r.get("Handler") or r.get("handler") or "?"),
                    _f(r.get("LastMinute")),
                    _f(r.get("LastHour")),
                    _f(r.get("Last24Hours")),
                    _f(r.get("AverageDuration")),
                    _f(r.get("AverageCPUMs")),
                ])
        return headers, rows

    def draw(self, win, store):
        h, w = win.getmaxyx()
        hosts = store.admin_hosts
        if not hosts:
            safe_addstr(win, 0, 0,
                        " Services — no admin URLs configured".ljust(w - 1),
                        theme.attr(theme.P_DIM))
            return

        flat: list = []
        ok_count = 0
        err_msg = None
        for host in hosts:
            snap = store.servicestats.get(host)
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
        safe_addstr(win, 0, 0, f" Services — {hdr_state}".ljust(w - 1), hdr_attr)

        if err_msg and ok_count == 0:
            safe_addstr(win, 2, 2, f"error: {err_msg}", theme.attr(theme.P_BAD))
            return
        if not flat:
            safe_addstr(win, 2, 2, "no service data available yet",
                        theme.attr(theme.P_DIM))
            return

        mx1 = max((_f(r.get("LastMinute")) for _, r in flat), default=0.0)
        host_col = 18 if multi else 0
        # Layout math: fixed columns on the left, then the bar absorbs
        # whatever's left (after reserving 20 cols for the trend spark).
        # The new cpu% column is 6 chars wide — a percentage with one
        # decimal plus a trailing space.
        fixed_left = (host_col + 1 if multi else 0) + 86
        trend_w = 20
        bar_w = max(10, w - fixed_left - trend_w - 4)
        hdr_line = (
            (f"{'host':<{host_col}} " if multi else "")
            + f"{'handler':<40} {'req/min':>8} {'req/h':>8} {'req/d':>10} "
            f"{'avg_ms':>8} {'cpu%':>5}  "
            f"{'last min':<{bar_w}}  {'trend':<{trend_w}}"
        )
        safe_addstr(win, 2, 0, hdr_line, theme.attr(theme.P_HEADER, curses.A_BOLD))
        safe_addstr(win, 3, 0, "─" * (w - 1), theme.attr(theme.P_DIM))

        body_top = 4
        body_h = h - body_top - 1
        if body_h <= 0:
            return

        # Decide compact vs tall layout the same way Plugins does. With
        # ~15 visible services and 30 rows of body the panel falls into
        # tall mode (per_service ~ 2 rows): the trend spark turns into
        # a multi-row vertical chart that uses the otherwise-empty
        # space below each handler row. With many services and a short
        # body, falls back to single-row.
        n_total = len(flat)
        max_per_service = max(1, body_h // n_total)
        per_service = (min(max_per_service, 6) if max_per_service >= 2
                       else 1)
        services_per_screen = max(1, body_h // per_service)

        if self.cursor >= len(flat):
            self.cursor = len(flat) - 1
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        if self.cursor >= self.scroll + services_per_screen:
            self.scroll = self.cursor - services_per_screen + 1
        max_scroll = max(0, len(flat) - services_per_screen)
        self.scroll = max(0, min(self.scroll, max_scroll))

        visible = flat[self.scroll : self.scroll + services_per_screen]
        for i, (host, r) in enumerate(visible):
            handler = str(r.get("Handler") or r.get("handler") or "?")
            m1 = _f(r.get("LastMinute"))
            m60 = _f(r.get("LastHour"))
            d24 = _f(r.get("Last24Hours"))
            avg = _f(r.get("AverageDuration"))
            cpu = _f(r.get("AverageCPUMs"))
            # cpu/wall ratio is the diagnostic: > 0.5 = CPU-bound
            # (green; optimisation lives in the on-CPU flame), < 0.1
            # = wait-bound (blue; off-CPU flame is the next stop),
            # in-between is mixed. "—" when AverageCPUMs is absent
            # (older spine) so the operator can see they need to
            # upgrade to read the ratio.
            if avg > 0 and cpu > 0:
                cpu_ratio = cpu / avg
                cpu_str = f"{cpu_ratio * 100:>4.0f}%"
                if cpu_ratio >= 0.5:
                    cpu_attr = theme.attr(theme.P_GOOD)
                elif cpu_ratio <= 0.1:
                    cpu_attr = theme.attr(theme.P_SPARK)
                else:
                    cpu_attr = theme.attr(theme.P_HEADER)
            else:
                cpu_str = "   — "
                cpu_attr = theme.attr(theme.P_DIM)
            y_top = body_top + i * per_service
            row_attr = curses.A_REVERSE if self.scroll + i == self.cursor else 0
            hist = store.service_history.get(host)
            cells = []
            if multi:
                cells.append((f"{host[:host_col-1]:<{host_col}} ",
                              theme.attr(theme.P_ACCENT)))
            cells += [
                (f"{handler[:40]:<40} ", 0),
                (f"{int(m1):>8d} ", 0),
                (f"{int(m60):>8d} ", 0),
                (f"{int(d24):>10d} ", 0),
                (f"{human_ms(avg):>8} ", theme.latency_color(avg)),
                (f"{cpu_str:>5}  ", cpu_attr),
                (hbar(m1, mx1, bar_w), theme.attr(theme.P_SPARK)),
                ("  ", 0),
            ]
            x = write_row(win, y_top, 0, cells, row_attr=row_attr)

            if per_service == 1:
                # Compact: single-row sparkline as before.
                trend = (hist.series(handler, "req_per_min",
                                      samples=trend_w + 1)
                         if hist else [])
                trend_str = (sparkline(trend, width=trend_w) if trend
                             else " " * trend_w)
                safe_addstr(win, y_top, x, trend_str,
                            theme.attr(theme.P_SPARK) | row_attr)
            else:
                # Tall: vertical chart of the trend spans `per_service`
                # rows in the trend column. Auto-scales per service.
                trend = (hist.series(handler, "req_per_min",
                                      samples=trend_w + 1)
                         if hist else [])
                if trend:
                    lines = vchart(trend, per_service, width=trend_w,
                                   maxval=0.0)
                    for j, line in enumerate(lines):
                        safe_addstr(win, y_top + j, x, line,
                                    theme.attr(theme.P_SPARK))
