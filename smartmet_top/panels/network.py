"""Network panel — host-wide TCP and per-NIC view.

Sections (top → bottom):

  1. Summary row: TCP retransmits, listen overflows, listen drops
     (rates, with sparkline of retransmits) — moved out of the Proc
     panel where it was crowded.
  2. Connection-state table: count + trend sparkline for every
     non-zero TCP state from /proc/net/tcp{,6}. The trio of states
     to watch is ESTABLISHED (the working set), TIME_WAIT
     (ephemeral-port exhaustion if it climbs into the thousands)
     and CLOSE_WAIT (a sign the application is leaking sockets).
  3. Listen-socket inspection: every LISTEN socket on the host with
     its current accept-queue depth. Sustained non-zero Recv-Q is
     the precursor to listen-drop alerts.
  4. Per-NIC bandwidth: one row per non-loopback interface (not
     filtered to busiest, unlike the Proc panel's compact view).
     Each row carries rx + tx rate plus rx + tx sparklines.

What it does NOT do (yet — follow-up TODOs):

  - tcptop-bpfcc integration for per-connection bandwidth ranking.
  - tcprtt-bpfcc integration for an RTT histogram.

Both depend on bcc-tools and are conceptually parallel to the
existing biolat / runqlat integrations. They will land in a
follow-up commit; the panel layout already has room for both
above the per-NIC list.
"""

from __future__ import annotations

import curses
from typing import List

from .. import theme
from ..snapshots.network import NetworkSnapshot
from ..widgets.bars import human_bytes, sparkline
from .base import Panel, safe_addstr, write_label, write_row, write_section_header


# Per-section toggles. Lowercase letters per the established case
# convention: uppercase = switch panels, lowercase = within-panel.
# t / c / l / b cycle each of the four Network sections on or off; a
# hidden section frees its vertical space for the rest.
_SECTION_KEYS = (
    ("t", "TCP host-wide"),
    ("c", "TCP connection states"),
    ("l", "Listen sockets"),
    ("b", "Per-interface bandwidth"),
)


class NetworkPanel(Panel):
    name = "Network"
    hotkey = "n"
    help_text = (
        "Host-wide TCP state, listen queues, retransmits, and per-NIC "
        "bandwidth. /proc/net/{tcp,tcp6,snmp,netstat,dev} only — no "
        "external tools required. Pair with the Proc panel's "
        "Network section, which auto-picks the busiest interfaces "
        "and is duplicated here in full detail."
    )
    panel_help = """\
TCP host-wide:
  retrans/s          retransmitted segments per second.
                     Sustained > 1/s = lossy network or peer
                     ring-buffer overflow.
  listen-overflow/s  SYNs dropped because the kernel's accept
                     queue was full. Application is not calling
                     accept() fast enough. Always a bug.
  listen-drop/s      same situation, different counter family.

TCP connection states (from /proc/net/tcp{,6}):
  ESTABLISHED  the working set — your active connections.
  TIME_WAIT    half-closed connections waiting for late
               packets. Normal up to a few thousand; > 5000
               sustained = ephemeral-port pressure (amber).
  CLOSE_WAIT   peer closed but the application has not yet
               called close(). > 100 sustained = the
               application is leaking sockets (red — definite
               bug).
  SYN_SENT/RECV  connections still in the 3-way handshake.
                 Persistent SYN_SENT = peer slow to ACK or
                 unreachable.
  FIN_WAIT1/2  graceful shutdown stages.
  LAST_ACK / CLOSING  same.

  Each state has a per-row trend sparkline so drift is visible
  before the absolute count crosses a threshold.

Listen sockets:
  Recv-Q is the CURRENT accept-queue depth (not the maximum
  configured by listen()). Sustained > 0 is the precursor to
  listen-drop alerts: connections done with the 3-way handshake
  but still waiting for the application to call accept().

Per-interface bandwidth:
  rx and tx in bytes/s plus sparklines. Loopback is skipped at
  source. Saturating an interface's line rate (e.g. ~120 MB/s
  on 1 Gbit, ~1.2 GB/s on 10 Gbit) caps total response
  throughput; the URLs panel will show every handler queueing.

Keys:
  t        toggle TCP host-wide section
  c        toggle Connection states section
  l        toggle Listen sockets section
  b        toggle Per-interface bandwidth section
  + / -    grow / shrink sparkline height (1-6 rows)
  e / E    export connection-state distribution as CSV / JSON
"""

    def __init__(self) -> None:
        # Spark height for the per-NIC + per-state rows. 2 rows
        # gives the same density as the Proc panel's default.
        self._spark_h = 2
        # Section visibility set. All sections start visible; toggles
        # below add / remove letters. The set is the source of truth
        # for both the renderer (skip drawing) and the section-header
        # chevron (▾ vs ▸).
        self._visible = {k for k, _ in _SECTION_KEYS}

    def handle_key(self, key, store):
        for letter, _label in _SECTION_KEYS:
            if key == ord(letter):
                if letter in self._visible:
                    self._visible.discard(letter)
                else:
                    self._visible.add(letter)
                return True
        if key in (ord("+"), ord("=")):
            self._spark_h = min(6, self._spark_h + 1)
            return True
        if key == ord("-"):
            self._spark_h = max(1, self._spark_h - 1)
            return True
        return False

    def export_snapshot(self, store):
        # Connection-state distribution is the most useful single
        # thing to dump. The snapshot returns ([], []) when there's
        # nothing yet; preserve the existing "nothing to export"
        # toast by returning (None, None) in that case.
        headers, rows = NetworkSnapshot.table(store)
        if not headers:
            return None, None
        return headers, rows

    def draw(self, win, store):
        h, w = win.getmaxyx()
        if not store.netstats_enabled:
            safe_addstr(win, 0, 0,
                        " Network — sampler not started yet".ljust(w - 1),
                        theme.attr(theme.P_TAB_ACTIVE))
            return
        # Header
        n_ifaces = len(store.netstats_iface_names())
        counts, listen_socks = store.netstats_states_latest()
        n_states = sum(1 for v in counts.values() if v > 0)
        n_listen = len(listen_socks)
        header = (f" Network — {n_ifaces} NIC(s), "
                  f"{n_states} TCP state(s), "
                  f"{n_listen} listen socket(s)")
        safe_addstr(win, 0, 0, header.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))
        row = 2
        # Hidden sections still render their one-row header (chevron +
        # greyed label) so the operator can see what's available and
        # which letter to press to bring it back. Visible sections
        # render their full body.
        row = self._draw_summary_row(win, store, row)
        row = self._draw_state_table(win, store, row, counts)
        row = self._draw_listen_table(win, store, row, listen_socks)
        row = self._draw_iface_table(win, store, row)
        self._draw_footer(win)

    # -------- sections -------------------------------------------------------

    def _draw_summary_row(self, win, store, row: int) -> int:
        h, w = win.getmaxyx()
        if row >= h:
            return row
        hidden = "t" not in self._visible
        write_section_header(win, row, "t", "TCP host-wide", hidden=hidden)
        row += 1
        if hidden or row + 1 + self._spark_h >= h:
            return row + (0 if hidden else 0)
        retrans, overflows, drops = store.netstats_tcp_series()
        latest_r = retrans[-1] if retrans else 0.0
        latest_o = overflows[-1] if overflows else 0.0
        latest_d = drops[-1] if drops else 0.0
        retrans_attr = (theme.attr(theme.P_BAD, curses.A_BOLD) if latest_r > 1
                        else theme.attr(theme.P_HEADER))
        drop_attr = (theme.attr(theme.P_BAD, curses.A_BOLD)
                     if (latest_o + latest_d) > 0
                     else theme.attr(theme.P_HEADER))
        cells = [
            (f"  retrans/s {latest_r:>6.1f}  ", retrans_attr),
            (f"listen-overflow/s {latest_o:>5.1f}  ", drop_attr),
            (f"listen-drop/s {latest_d:>5.1f}  ", drop_attr),
        ]
        x = write_row(win, row, 0, cells)
        spark_w = max(15, min(50, w - x - 2))
        if retrans:
            self._draw_spark_at(win, row, x, retrans, spark_w,
                                 theme.attr(theme.P_SPARK))
        return row + self._spark_h + 1

    def _draw_state_table(self, win, store, row: int,
                          counts) -> int:
        h, w = win.getmaxyx()
        if row >= h:
            return row
        hidden = "c" not in self._visible
        write_section_header(win, row, "c", "TCP connection states",
                              hidden=hidden)
        row += 1
        if hidden or row + 2 >= h:
            return row
        # Header line
        cells = [
            ("  state            ", theme.attr(theme.P_HEADER, curses.A_BOLD)),
            ("count   ", theme.attr(theme.P_HEADER, curses.A_BOLD)),
            ("trend",   theme.attr(theme.P_HEADER, curses.A_BOLD)),
        ]
        write_row(win, row, 0, cells)
        row += 1
        # One row per non-zero state, sorted by count desc.
        present = sorted(((k, v) for k, v in counts.items() if v > 0),
                         key=lambda kv: -kv[1])
        for state, count in present:
            if row + self._spark_h >= h - 2:
                break
            attr = self._state_color(state, count)
            cells = [
                (f"  {state:<16} ", attr),
                (f"{count:>6}  ", attr),
            ]
            x = write_row(win, row, 0, cells)
            series = store.netstats_state_series(state)
            spark_w = max(15, min(50, w - x - 2))
            if series:
                self._draw_spark_at(win, row, x, series, spark_w,
                                    theme.attr(theme.P_SPARK))
            row += self._spark_h
        return row + 1

    def _draw_listen_table(self, win, store, row: int,
                           listen_socks) -> int:
        h, w = win.getmaxyx()
        if row >= h:
            return row
        hidden = "l" not in self._visible
        # Render the header even if there are no listen sockets — the
        # ``[l]`` chip stays so the operator can find the toggle.
        write_section_header(win, row, "l", "Listen sockets", hidden=hidden)
        row += 1
        if hidden or not listen_socks or row + 2 >= h:
            return row
        cells = [
            ("  port      ", theme.attr(theme.P_HEADER, curses.A_BOLD)),
            ("Recv-Q (current backlog)",
             theme.attr(theme.P_HEADER, curses.A_BOLD)),
        ]
        write_row(win, row, 0, cells)
        row += 1
        # Listening sockets sorted by port. Recv-Q > 0 sustained is
        # the precursor to listen drops; we colour > 0 amber.
        for port, recv_q in sorted(listen_socks):
            if row >= h - 2:
                break
            attr = (theme.attr(theme.P_WARN, curses.A_BOLD) if recv_q > 0
                    else theme.attr(theme.P_HEADER))
            cells = [
                (f"  {port:<8} ", attr),
                (f"{recv_q:>6}", attr),
            ]
            write_row(win, row, 0, cells)
            row += 1
        return row + 1

    def _draw_iface_table(self, win, store, row: int) -> int:
        h, w = win.getmaxyx()
        ifaces = store.netstats_iface_names()
        if row >= h:
            return row
        hidden = "b" not in self._visible
        write_section_header(win, row, "b", "Per-interface bandwidth",
                              hidden=hidden)
        row += 1
        if hidden or not ifaces or row + 2 >= h:
            return row
        for iface in ifaces:
            if row + self._spark_h >= h - 2:
                break
            rx, tx = store.netstats_iface_series(iface)
            rx_now = rx[-1] if rx else 0.0
            tx_now = tx[-1] if tx else 0.0
            cells = [
                (f"  {iface:<10} ", theme.attr(theme.P_HEADER, curses.A_BOLD)),
                (f"rx {human_bytes(rx_now):>10}/s  ", 0),
            ]
            x = write_row(win, row, 0, cells)
            spark_w_each = max(10, min(30, (w - x - 30) // 2))
            if rx:
                self._draw_spark_at(win, row, x, rx, spark_w_each,
                                    theme.attr(theme.P_SPARK))
            x += spark_w_each + 2
            safe_addstr(win, row, x, f"tx {human_bytes(tx_now):>10}/s  ",
                        theme.attr(theme.P_HEADER))
            x += 18
            if tx and x + spark_w_each < w:
                self._draw_spark_at(win, row, x, tx, spark_w_each,
                                    theme.attr(theme.P_SPARK))
            row += self._spark_h
        return row + 1

    # -------- helpers --------------------------------------------------------

    def _draw_spark_at(self, win, y, x, values, width, attr) -> None:
        """Multi-row Braille sparkline. Same shape as the Proc panel's
        helper — we replicate it here rather than import to keep this
        panel self-contained."""
        from ..widgets.bars import vchart
        h_avail = win.getmaxyx()[0] - y
        height = min(self._spark_h, h_avail)
        if not values:
            return
        if height <= 1:
            safe_addstr(win, y, x, sparkline(values, width=width), attr)
            return
        rows = vchart(values, height=height, width=width)
        for j, line in enumerate(rows):
            safe_addstr(win, y + j, x, line, attr)

    @staticmethod
    def _state_color(state: str, count: int) -> int:
        """Colour a connection-state row by severity:

          - TIME_WAIT > 5000 : amber  (ephemeral-port pressure)
          - CLOSE_WAIT > 100 : red    (the application is leaking
                                       sockets — definite bug)
          - everything else  : neutral.
        """
        if state == "CLOSE_WAIT" and count > 100:
            return theme.attr(theme.P_BAD, curses.A_BOLD)
        if state == "TIME_WAIT" and count > 5000:
            return theme.attr(theme.P_WARN, curses.A_BOLD)
        if state == "ESTABLISHED":
            return theme.attr(theme.P_GOOD)
        return theme.attr(theme.P_HEADER)

    def _draw_footer(self, win) -> None:
        h, w = win.getmaxyx()
        if h < 2:
            return
        hot = theme.attr(theme.P_MNEMONIC, curses.A_BOLD | curses.A_UNDERLINE)
        base = theme.attr(theme.P_TITLE)
        x = 0
        safe_addstr(win, h - 1, x, " toggle ", base); x += 8
        # One chip per section, reflecting visibility — letter is
        # underlined+bold in red when active, plain when hidden, so
        # the footer doubles as a state indicator.
        for letter, _label in _SECTION_KEYS:
            on = letter in self._visible
            chip = f"[{letter}]"
            chip_attr = (theme.attr(theme.P_MNEMONIC, curses.A_BOLD)
                         if on else theme.attr(theme.P_DIM))
            safe_addstr(win, h - 1, x, chip, chip_attr); x += 3
            safe_addstr(win, h - 1, x, " ", base); x += 1
        safe_addstr(win, h - 1, x, "  ", base); x += 2
        x = write_label(win, h - 1, x, "+", 0, base, hot)
        x = write_label(win, h - 1, x, "/", 0, base, base)
        x = write_label(win, h - 1, x, "-", 0, base, hot)
        x = write_label(win, h - 1, x,
                        f" spark h={self._spark_h}   ", 0, base, base)
        x = write_label(win, h - 1, x, "e", 0, base, hot)
        x = write_label(win, h - 1, x, "/", 0, base, base)
        x = write_label(win, h - 1, x, "E", 0, base, hot)
        x = write_label(win, h - 1, x, " export", 0, base, base)
        if x < w - 1:
            safe_addstr(win, h - 1, x, " " * (w - x - 1), base)
