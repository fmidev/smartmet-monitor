"""Active requests panel — admin-plugin ?what=activerequests."""

from __future__ import annotations

import curses
import time

from .. import theme
from .base import Panel, safe_addstr, write_row


class ActivePanel(Panel):
    name = "Active"
    hotkey = "a"
    help_text = "In-flight requests from /admin?what=activerequests."

    def __init__(self):
        self.scroll = 0

    def handle_key(self, key, store):
        if key == curses.KEY_UP:
            self.scroll = max(0, self.scroll - 1)
        elif key == curses.KEY_DOWN:
            self.scroll += 1
        elif key == curses.KEY_PPAGE:
            self.scroll = max(0, self.scroll - 10)
        elif key == curses.KEY_NPAGE:
            self.scroll += 10
        else:
            return False
        return True

    def export_snapshot(self, store):
        headers = ["host", "id", "duration_s", "client_ip", "apikey", "request"]
        rows = []
        for host in store.admin_hosts:
            snap = store.activerequests.get(host)
            if snap is None or not snap.ok:
                continue
            for r in snap.rows or []:
                try:
                    d = float(r.get("Duration") or r.get("duration") or 0)
                except (ValueError, TypeError):
                    d = 0.0
                rows.append([
                    host,
                    str(r.get("Id") or r.get("id") or ""),
                    round(d, 3),
                    str(r.get("ClientIP") or r.get("clientip") or ""),
                    str(r.get("Apikey") or r.get("apikey") or "-"),
                    str(r.get("RequestString") or r.get("requeststring") or ""),
                ])
        return headers, rows

    def draw(self, win, store):
        h, w = win.getmaxyx()
        hosts = store.admin_hosts
        if not hosts:
            safe_addstr(win, 0, 0, " Active — no admin URLs configured".ljust(w - 1),
                        theme.attr(theme.P_DIM))
            return

        def dur(r):
            try:
                return float(r.get("Duration") or r.get("duration") or 0)
            except (ValueError, TypeError):
                return 0.0

        flat: list = []
        ok_count = 0
        err_msg = None
        for host in hosts:
            snap = store.activerequests.get(host)
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
        safe_addstr(win, 0, 0, f" Active — {hdr_state}".ljust(w - 1), hdr_attr)

        if err_msg and ok_count == 0:
            safe_addstr(win, 2, 2, f"error: {err_msg}", theme.attr(theme.P_BAD))
            return
        if not flat:
            safe_addstr(win, 2, 2, "no active requests", theme.attr(theme.P_DIM))
            return

        flat.sort(key=lambda it: dur(it[1]), reverse=True)
        host_col = 18 if multi else 0
        hdr_line = (
            (f"{'host':<{host_col}} " if multi else "")
            + f"{'id':>6} {'dur_s':>7} {'client':<20} {'apikey':<20}  request"
        )
        safe_addstr(win, 2, 0, hdr_line,
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        safe_addstr(win, 3, 0, "─" * (w - 1), theme.attr(theme.P_DIM))

        body_top = 4
        body_h = h - body_top - 1
        if self.scroll >= len(flat):
            self.scroll = max(0, len(flat) - 1)

        for i, (host, r) in enumerate(flat[self.scroll : self.scroll + body_h]):
            rid = str(r.get("Id") or r.get("id") or "?")
            d = dur(r)
            cip = str(r.get("ClientIP") or r.get("clientip") or "?")
            ak = str(r.get("Apikey") or r.get("apikey") or "-")
            req = str(r.get("RequestString") or r.get("requeststring") or "")
            dur_attr = theme.duration_color(d)
            cells = []
            if multi:
                cells.append((f"{host[:host_col-1]:<{host_col}} ",
                              theme.attr(theme.P_ACCENT)))
            cells += [
                (f"{rid:>6} ", 0),
                (f"{d:>7.1f} ", dur_attr),
                (f"{cip[:20]:<20} ", 0),
                (f"{ak[:20]:<20}  ", theme.attr(theme.P_ACCENT)),
                (req, 0),
            ]
            write_row(win, body_top + i, 0, cells)
