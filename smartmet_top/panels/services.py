"""Services panel — admin-plugin ?what=servicestats."""

from __future__ import annotations

import curses
import time

from .. import theme
from ..widgets.bars import hbar, human_count, human_ms, sparkline
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
        else:
            return False
        return True

    def export_snapshot(self, store):
        headers = ["host", "handler", "req_per_min", "req_per_hour",
                   "req_per_day", "avg_ms"]
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
        hdr_line = (
            (f"{'host':<{host_col}} " if multi else "")
            + f"{'handler':<40} {'req/min':>8} {'req/h':>8} {'req/d':>10} "
            f"{'avg_ms':>8}  {'last min':<25}  {'trend':<20}"
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
            handler = str(r.get("Handler") or r.get("handler") or "?")
            m1 = _f(r.get("LastMinute"))
            m60 = _f(r.get("LastHour"))
            d24 = _f(r.get("Last24Hours"))
            avg = _f(r.get("AverageDuration"))
            row_attr = curses.A_REVERSE if self.scroll + i == self.cursor else 0
            hist = store.service_history.get(host)
            trend = hist.series(handler, "req_per_min", samples=20) if hist else []
            trend_str = sparkline(trend, width=20) if trend else " " * 20
            cells = []
            if multi:
                cells.append((f"{host[:host_col-1]:<{host_col}} ",
                              theme.attr(theme.P_ACCENT)))
            cells += [
                (f"{handler[:40]:<40} ", 0),
                (f"{m1:>8.1f} ", 0),
                (f"{m60:>8.1f} ", 0),
                (f"{d24:>10.1f} ", 0),
                (f"{human_ms(avg):>8}  ", theme.latency_color(avg)),
                (hbar(m1, mx1, 25), theme.attr(theme.P_SPARK)),
                ("  ", 0),
                (trend_str, theme.attr(theme.P_SPARK)),
            ]
            write_row(win, body_top + i, 0, cells, row_attr=row_attr)
