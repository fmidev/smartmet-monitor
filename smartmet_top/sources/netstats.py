"""Host network counters — TCP retransmits + listen overflows + NIC bandwidth.

Pulled from `/proc/net/snmp`, `/proc/net/netstat`, and `/proc/net/dev`
on every poll cycle. All three files are flat ASCII counters that have
been part of Linux since forever, so this source has no kernel-version
or eBPF dependency — it runs on the smallest VM as comfortably as on
a bare-metal SmartMet host.

Counters tracked (cumulative — the Store converts to per-second rates
across successive samples):

  * `Tcp.RetransSegs`            — segments retransmitted, host-wide.
  * `TcpExt.ListenOverflows`     — accept queue full at SYN time.
  * `TcpExt.ListenDrops`         — listen queue overflow drops.
  * Per-NIC `rx_bytes / tx_bytes` from /proc/net/dev.

Loopback (`lo`) is skipped from the NIC list — it dominates any
SmartMet host where backend ↔ frontend talks over local sockets, and
it is never the bottleneck so showing it would only crowd the panel.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Dict, List, Optional, Tuple

from . import detectors


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _parse_proc_net_snmp(text: str) -> Dict[str, Dict[str, int]]:
    """Two-line-per-protocol format: header line then values line.

    Returns {"Tcp": {"RetransSegs": N, ...}, "TcpExt": {...}, ...}
    """
    out: Dict[str, Dict[str, int]] = {}
    keys: Dict[str, List[str]] = {}
    for line in text.splitlines():
        proto, _, rest = line.partition(":")
        if not rest:
            continue
        proto = proto.strip()
        fields = rest.split()
        if proto in keys:
            # values line
            row = {k: _safe_int(v) for k, v in zip(keys[proto], fields)}
            out[proto] = row
            del keys[proto]
        else:
            # header line — record the field names
            keys[proto] = fields
    return out


def _safe_int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


# /proc/net/dev format:
#   Inter-|   Receive                                                |  Transmit
#    face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed
#       lo: 10576 ...
_DEV_LINE_RE = re.compile(
    r"^\s*(?P<iface>[^:\s]+)\s*:\s*(?P<rest>.+)$"
)


def parse_proc_net_dev(text: str) -> Dict[str, Tuple[int, int]]:
    """Per-interface (rx_bytes, tx_bytes) from /proc/net/dev.

    The header lines (Inter-| / face |...) are ignored because they
    don't match the regex anchored on `name:`. Loopback is filtered
    out — it's never the operational bottleneck and showing it would
    swamp real interfaces on a server where backend ↔ frontend run
    over local sockets.
    """
    out: Dict[str, Tuple[int, int]] = {}
    for line in text.splitlines():
        m = _DEV_LINE_RE.match(line)
        if not m:
            continue
        iface = m.group("iface")
        if iface == "lo":
            continue
        cols = m.group("rest").split()
        if len(cols) < 16:
            continue
        rx = _safe_int(cols[0])
        tx = _safe_int(cols[8])
        out[iface] = (rx, tx)
    return out


def _read_tcp_counters() -> Tuple[int, int, int]:
    """Returns (retrans, listen_overflows, listen_drops). Missing
    fields are reported as 0 — happens on RHEL 8 with very old
    sysctls or in containers with restricted /proc views."""
    try:
        snmp = _parse_proc_net_snmp(_read_text("/proc/net/snmp"))
    except OSError:
        snmp = {}
    try:
        netstat = _parse_proc_net_snmp(_read_text("/proc/net/netstat"))
    except OSError:
        netstat = {}
    retrans = snmp.get("Tcp", {}).get("RetransSegs", 0)
    overflows = netstat.get("TcpExt", {}).get("ListenOverflows", 0)
    drops = netstat.get("TcpExt", {}).get("ListenDrops", 0)
    return retrans, overflows, drops


def _read_dev_counters() -> Dict[str, Tuple[int, int]]:
    try:
        return parse_proc_net_dev(_read_text("/proc/net/dev"))
    except OSError:
        return {}


# /proc/net/tcp{,6} state codes. Hex in the file; we count by the
# decimal value here. The state field is column 4 (index 3 after the
# `sl` row number).
_TCP_STATES = {
    1:  "ESTABLISHED",
    2:  "SYN_SENT",
    3:  "SYN_RECV",
    4:  "FIN_WAIT1",
    5:  "FIN_WAIT2",
    6:  "TIME_WAIT",
    7:  "CLOSE",
    8:  "CLOSE_WAIT",
    9:  "LAST_ACK",
    10: "LISTEN",
    11: "CLOSING",
}


def parse_proc_net_tcp(text: str) -> Tuple[Dict[str, int], List[Tuple[int, int]]]:
    """Return ({state_name: count}, [(listen_port, accept_backlog), …]).

    For each line, column 4 is the state in hex, column 1 is
    `local_address:port` (also hex), column 5 is `tx_queue:rx_queue`.
    On LISTEN sockets the kernel reports the *current* accept queue
    depth as rx_queue — exactly the "Recv-Q" `ss -lnt` shows. Anything
    non-zero there is a transient backlog; sustained > 0 over multiple
    samples is the precursor to listen-drop alerts.
    """
    counts: Dict[str, int] = {name: 0 for name in _TCP_STATES.values()}
    listen: List[Tuple[int, int]] = []
    first = True
    for line in text.splitlines():
        if first:
            first = False  # skip header row
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            state = int(parts[3], 16)
        except ValueError:
            continue
        name = _TCP_STATES.get(state)
        if name is None:
            continue
        counts[name] += 1
        if state == 10:  # LISTEN
            try:
                _, port_hex = parts[1].rsplit(":", 1)
                port = int(port_hex, 16)
            except (ValueError, IndexError):
                port = 0
            try:
                _, rx_q_hex = parts[4].split(":", 1)
                rx_q = int(rx_q_hex, 16)
            except (ValueError, IndexError):
                rx_q = 0
            listen.append((port, rx_q))
    return counts, listen


def _read_tcp_state_counts() -> Tuple[Dict[str, int], List[Tuple[int, int]]]:
    """Aggregate state counts from both /proc/net/tcp and /proc/net/tcp6."""
    total: Dict[str, int] = {name: 0 for name in _TCP_STATES.values()}
    all_listen: List[Tuple[int, int]] = []
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            text = _read_text(path)
        except OSError:
            continue
        counts, listen = parse_proc_net_tcp(text)
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
        all_listen.extend(listen)
    return total, all_listen


async def netstats_loop(store, interval: float = 2.0) -> None:
    """Sample TCP + NIC counters at `interval` seconds. Always-on:
    no capability detection, no external tools. The first cycle just
    seeds the cumulative baselines so rates are computed from sample 2
    onward."""
    store.netstats_enabled = True
    store.netstats_status = "sampling /proc/net counters"
    last_tcp: Optional[Tuple[int, int, int]] = None
    last_dev: Dict[str, Tuple[int, int]] = {}
    last_ts: float = 0.0
    while True:
        now = time.time()
        retrans, overflows, drops = _read_tcp_counters()
        dev = _read_dev_counters()
        # State counts + listen-queue depths come from /proc/net/tcp
        # directly. They are point-in-time snapshots, not rates; the
        # Store keeps a small history so the Network panel can show a
        # trend if the state distribution drifts (TIME_WAIT pile-up,
        # CLOSE_WAIT leak …).
        state_counts, listen_socks = _read_tcp_state_counts()
        store.netstats_record_states(now, state_counts, listen_socks)
        if last_tcp is not None and last_ts > 0 and now > last_ts:
            dt = now - last_ts
            rrate = max(0, retrans - last_tcp[0]) / dt
            orate = max(0, overflows - last_tcp[1]) / dt
            drate = max(0, drops - last_tcp[2]) / dt
            store.netstats_record_tcp(now, rrate, orate, drate)
            detectors.detect_netstats_retrans(store)
            detectors.detect_netstats_listen_drops(store)
            for iface, (rx, tx) in dev.items():
                last_rx_tx = last_dev.get(iface)
                if last_rx_tx is None:
                    continue
                rx_rate = max(0, rx - last_rx_tx[0]) / dt
                tx_rate = max(0, tx - last_rx_tx[1]) / dt
                store.netstats_record_iface(now, iface, rx_rate, tx_rate)
        last_tcp = (retrans, overflows, drops)
        last_dev = dev
        last_ts = now
        await asyncio.sleep(interval)
