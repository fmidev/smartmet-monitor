"""Active requests panel — admin-plugin ?what=activerequests."""

from __future__ import annotations

import curses
import time

from .. import theme
from ..widgets.bars import sparkline, vchart
from .base import Panel, safe_addstr, write_row


class ActivePanel(Panel):
    name = "Active"
    hotkey = "a"
    help_text = "In-flight requests from /admin?what=activerequests."
    panel_help = """\
What is being processed RIGHT NOW. Polled from
`?what=activerequests` on the admin endpoint, refreshed every
admin cycle.

Top of panel:
  In-flight count sparkline tracking how many requests have
  been simultaneously active over the recent admin-poll
  history (~10 min at the default cadence). The peak number
  shown in the header tells you the worst burst seen since
  the app started polling.

Columns (each row is one in-flight request):
  host        admin host the request was reported from.
              Multi-host setups: `?what=activerequests` is
              polled per host so requests on different
              backends sit in the same table together.
  id          spine's internal request id — useful when
              cross-referencing with logs or with
              `?what=lastrequests` on the same host.
  dur_s       seconds since the request started, on the
              moment of the most recent admin poll. Coloured
              by latency: green up to a few seconds, amber
              into the tens, red beyond — long-running
              requests stand out at a glance.
  client      remote IP of the requestor.
  apikey      FMI API key string if the request carried one,
              "-" otherwise. Useful for "who is hammering us
              right now?".
  request     the request line, query string included.

Reading the panel:
  - Many short-duration rows = healthy throughput.
  - A few rows with very large dur_s = stuck or slow
    upstream; cross-check the URLs panel for that handler's
    p95.
  - Pile-up of in-flight count alongside listen-drops in the
    Network panel = the application can no longer keep up
    with new connections.

Keys:
  ↑ ↓ PgUp PgDn   scroll
  e / E           export as CSV / JSON
"""

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
        # Aggregate active-count history across hosts (most operators
        # have one host; with multi-host, sum the in-flight counts so
        # the operator sees total load).
        agg_history: list = []
        any_history = False
        for host in hosts:
            buf = store.active_count_history.get(host)
            if not buf:
                continue
            samples = list(buf)
            any_history = True
            if not agg_history:
                agg_history = samples[:]
            else:
                # Pad shorter list with leading zeros to align.
                if len(samples) < len(agg_history):
                    samples = [0] * (len(agg_history) - len(samples)) + samples
                elif len(samples) > len(agg_history):
                    agg_history = ([0] * (len(samples) - len(agg_history))
                                   + agg_history)
                agg_history = [a + b for a, b in zip(agg_history, samples)]

        # Header line
        current = agg_history[-1] if agg_history else len(flat)
        peak = max(agg_history) if agg_history else current
        hdr_state = (f"{ok_count}/{len(hosts)} hosts OK"
                     if multi else
                     ("OK" if ok_count == len(hosts) else "ERROR"))
        hdr_attr = (theme.attr(theme.P_TAB_ACTIVE) if ok_count == len(hosts)
                    else theme.attr(theme.P_BAD, curses.A_BOLD))
        header_text = (
            f" Active — {hdr_state}  in-flight={current}  peak={peak}"
        )
        safe_addstr(win, 0, 0, header_text.ljust(w - 1), hdr_attr)

        if err_msg and ok_count == 0:
            safe_addstr(win, 2, 2, f"error: {err_msg}", theme.attr(theme.P_BAD))
            return

        # Active-count sparkline at the top of the panel — vchart at
        # auto-scale so the recent peak fills the rendered height. The
        # operator's "one dot per request" intuition holds at low
        # loads; at high loads (100+) the chart auto-scales.
        chart_top = 1
        chart_h = 4 if h >= 16 else (2 if h >= 10 else 0)
        if any_history and chart_h > 0:
            chart_w = max(20, w - 12)
            lines = vchart(agg_history, chart_h, width=chart_w, maxval=0.0)
            for j, line in enumerate(lines):
                safe_addstr(win, chart_top + j, 6, line,
                            theme.attr(theme.P_SPARK))
            # Y-axis labels: peak at top, 0 at bottom.
            if peak > 0:
                safe_addstr(win, chart_top, 0, f"{peak:>5d} ",
                            theme.attr(theme.P_DIM))
            safe_addstr(win, chart_top + chart_h - 1, 0,
                        f"{0:>5d} ", theme.attr(theme.P_DIM))

        list_top = chart_top + chart_h + 1 if (any_history and chart_h > 0) else 2

        if not flat:
            safe_addstr(win, list_top, 2, "no active requests right now",
                        theme.attr(theme.P_DIM))
            return

        flat.sort(key=lambda it: dur(it[1]), reverse=True)
        host_col = 18 if multi else 0
        hdr_line = (
            (f"{'host':<{host_col}} " if multi else "")
            + f"{'id':>6} {'dur_s':>7} {'client':<20} {'apikey':<20}  request"
        )
        safe_addstr(win, list_top, 0, hdr_line,
                    theme.attr(theme.P_HEADER, curses.A_BOLD))
        safe_addstr(win, list_top + 1, 0, "─" * (w - 1),
                    theme.attr(theme.P_DIM))

        body_top = list_top + 2
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
