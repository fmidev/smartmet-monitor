"""Threshold-based detectors that turn raw metric samples into alerts.

Each detector is a small pure-ish function that:

  1. Reads its source's recent samples from the Store.
  2. Decides whether a trouble pattern is currently active.
  3. Calls `store.upsert_alert(...)` with a stable id when it is.

Detectors are called from each source's loop after a fresh sample is
recorded. Keeping them in this module rather than inline in the
source files lets the threshold rationales sit side-by-side, so the
"why is this number `100`?" answer is one click away from any other
threshold's answer.

The thresholds here intentionally match the colour bands the panels
already use so the operator's "this looks red" instinct and the
alert system's machine-readable judgement agree.
"""

from __future__ import annotations

import time
from typing import Optional

from ..state.alerts import Alert


# ---- helpers ----------------------------------------------------------------


def _alert(severity: str, detector: str, title: str, detail: str,
           id_: Optional[str] = None,
           suggested_panel: Optional[str] = None,
           suggested_action: Optional[str] = None,
           docs_anchor: Optional[str] = None) -> Alert:
    if id_ is None:
        id_ = detector
    return Alert(
        id=id_,
        severity=severity,
        detector=detector,
        title=title,
        detail=detail,
        suggested_panel=suggested_panel,
        suggested_action=suggested_action,
        docs_anchor=docs_anchor,
    )


# ---- per-source detectors ---------------------------------------------------


def detect_majflt_storm(store, info, samples) -> None:
    """Major page-fault rate sustained above the colour-band threshold.

    Mirrors the panel's "red over 100/s" choice. We need three
    consecutive sample-pair rates above the line so a single noisy
    delta (e.g. a /proc/PID/stat read that briefly raced with a
    fork) does not raise an alert. Three rate windows = four
    samples.
    """
    if len(samples) < 4:
        return
    rates = []
    for i in range(-3, 0):
        a, b = samples[i - 1], samples[i]
        dt = b.ts - a.ts
        if dt <= 0:
            return
        rates.append(max(0, b.majflt - a.majflt) / dt)
    if min(rates) <= 100:
        return
    latest_rate = rates[-1]
    store.upsert_alert(_alert(
        severity="warn",
        detector="majflt-storm",
        id_=f"majflt-storm:{info.pid}",
        title=f"Major page-fault storm on pid {info.pid} "
              f"({latest_rate:.0f}/s)",
        detail=(
            "smartmetd is reading pages from disk that should already "
            "be cached. Sustained > 100/s for several samples — the "
            "kernel had to satisfy that many synchronous block reads "
            "in the last few seconds.\n\n"
            "Likely causes:\n"
            "  - A fresh model run pushed the working set out of "
            "page cache (most common on a SmartMet host).\n"
            "  - Another process on this host suddenly demanded RAM "
            "and stole pages from smartmetd.\n"
            "  - A plugin is touching previously-unread files (broad "
            "param= / producer= enumeration).\n\n"
            "Look at Block I/O latency next: if its p95 is also high, "
            "the disk has caught the fault traffic; if I/O looks "
            "fine, the storage is keeping up and the latency is "
            "purely the synchronous reads themselves."
        ),
        suggested_panel="p",
        suggested_action="open Proc → Block I/O latency to confirm storage caught it",
        docs_anchor="major-page-faults-proc-panel",
    ))


def detect_biolat_slow(store) -> None:
    """Block-device p95 sustained over 10 ms — the colour-band threshold
    for the panel's "red" treatment plus a sustained-window check.
    """
    samples = list(store.biolat_samples)
    if len(samples) < 3:
        return
    last3 = samples[-3:]
    if min(s[2] for s in last3) <= 10_000:  # p95 in microseconds
        return
    p95_now = last3[-1][2]
    iops_now = last3[-1][4]
    store.upsert_alert(_alert(
        severity="warn",
        detector="biolat-slow",
        title=f"Block I/O slow (p95 {p95_now/1000:.0f} ms, "
              f"IOPS {iops_now})",
        detail=(
            "Storage p95 has been over 10 ms for several windows. "
            "On healthy SSD-backed servers p95 lives in the "
            "hundreds of microseconds; multi-millisecond p95 is the "
            "device or its queue saturating.\n\n"
            "Likely causes:\n"
            "  - Major page faults pushing a wave of synchronous "
            "reads (check the Page faults sparkline above).\n"
            "  - A backup, dd, or snapshot running on the same "
            "volume.\n"
            "  - Network-attached storage path congestion.\n"
            "  - The SSD is reaching its endurance limit "
            "(IOPS flat/falling while p95 climbs is the tell).\n\n"
            "If page faults spike at the same time, the page-fault "
            "alert above is the root cause — fix that first."
        ),
        suggested_panel="p",
        suggested_action="cross-check the Page faults sparkline; if flat, look at iostat",
        docs_anchor="block-i-o-latency-proc-panel",
    ))


def detect_runqlat_stalls(store) -> None:
    """Run-queue p95 ≥ 1 ms sustained — same threshold as the panel's
    red colouring. Indicates scheduler-side latency, especially
    important on virtualised hosts."""
    samples = list(store.runqlat_samples)
    if len(samples) < 3:
        return
    last3 = samples[-3:]
    if min(s[2] for s in last3) < 1000:  # 1 ms in microseconds
        return
    p95_now = last3[-1][2]
    store.upsert_alert(_alert(
        severity="warn",
        detector="runqlat-stalls",
        title=f"Scheduler stalls (run-queue p95 {p95_now/1000:.1f} ms)",
        detail=(
            "Threads are sitting ready-but-not-running for over a "
            "millisecond at p95. On bare metal this should be tens "
            "of microseconds; on a healthy VM the same. Sustained "
            "ms-scale waits mean the scheduler is the bottleneck, "
            "not the CPU work itself.\n\n"
            "Likely causes:\n"
            "  - Container CFS quota throttling (check "
            "/sys/fs/cgroup/$UNIT/cpu.stat for nr_throttled / "
            "throttled_time).\n"
            "  - Noisy-neighbour VM pinning the same physical "
            "cores (vmstat 1 → `st` column climbs in tandem).\n"
            "  - Too many runnable threads for too few CPUs.\n"
            "  - Real-time tasks pre-empting smartmetd (rare; "
            "chrt -p PID confirms).\n\n"
            "If CPU utilisation looks idle but request p95 is up, "
            "this graph is the proof."
        ),
        suggested_panel="p",
        suggested_action="check vmstat steal column; cgroup cpu.stat for throttled_time",
        docs_anchor="run-queue-latency-proc-panel",
    ))


def detect_perfstat_low_ipc(store) -> None:
    """IPC sustained < 0.3 — same threshold as the panel's red colouring.
    Single-strongest signal that the CPU work is memory-bound."""
    samples = list(store.perfstat_samples)
    if len(samples) < 2:
        return
    last2 = samples[-2:]
    if max(s[2] for s in last2) >= 0.3 or last2[-1][2] == 0:
        return
    pid = last2[-1][1]
    ipc_now = last2[-1][2]
    cm_now = last2[-1][3]
    store.upsert_alert(_alert(
        severity="warn",
        detector="perfstat-low-ipc",
        id_=f"perfstat-low-ipc:{pid}",
        title=f"CPU stalled on pid {pid} (IPC {ipc_now:.2f})",
        detail=(
            f"Instructions-per-cycle has been below 0.3 — the CPU is "
            f"spending most of its time waiting, not computing. "
            f"Cache miss rate is currently {cm_now*100:.1f}%.\n\n"
            "Likely causes:\n"
            "  - Memory-bound hot loop (large hash maps, deep "
            "pointer chains, virtual-call-heavy traversal).\n"
            "  - Working-set-per-core larger than L3 cache "
            "(if cache miss > 30% the panel will also be red).\n"
            "  - Host-level CPU contention — runqlat will be "
            "elevated at the same time if so.\n"
            "  - Old binary built without modern -march=, missing "
            "branch hints (branch miss rate stays elevated even "
            "across reloads).\n\n"
            "Open the on-CPU flame to find which function is hot, "
            "then the off-CPU flame to confirm the work is really "
            "CPU-bound rather than blocking."
        ),
        suggested_panel="f",
        suggested_action="open Flame view to find the hot function",
        docs_anchor="cpu-efficiency---ipc--cache--branch-miss-rates-proc-panel",
    ))


def detect_netstats_retrans(store) -> None:
    """TCP retransmits sustained > 1/s — anything that high is a clear
    network or peer problem on production servers."""
    tcp = list(store.netstats_tcp)
    if len(tcp) < 3:
        return
    last3 = tcp[-3:]
    if min(s[1] for s in last3) <= 1.0:
        return
    rrate = last3[-1][1]
    store.upsert_alert(_alert(
        severity="warn",
        detector="netstats-retrans",
        title=f"TCP retransmits sustained ({rrate:.1f}/s)",
        detail=(
            "Retransmit rate has been above 1/s for several samples. "
            "On a steady-state production server this should be flat "
            "at 0 with the occasional single-segment blip on long-"
            "lived connections.\n\n"
            "Likely causes:\n"
            "  - Lossy network path between this host and a client "
            "subnet (cable / NIC / switch port / firewall).\n"
            "  - Peer's NIC ring buffer full (the receiving host "
            "will also report retrans if so).\n"
            "  - Overloaded firewall or load balancer dropping "
            "segments under burst.\n\n"
            "Run `ss -s` and `nstat -az TcpRetransSegs "
            "TcpExtTCPLostRetransmit` on this host. Same on a peer: "
            "if only one side reports loss, it's local; both "
            "sides, the path is the suspect."
        ),
        suggested_panel="p",
        suggested_action="run `ss -s` here and on a peer; compare retrans counters",
        docs_anchor="network--tcp-retransmits--listen-drops--nic-bandwidth-proc-panel",
    ))


def detect_netstats_listen_drops(store) -> None:
    """Listen-queue overflows or drops at any positive rate — the
    application is failing to call accept() fast enough. Always a bug
    when it appears, hence severity=crit rather than warn."""
    tcp = list(store.netstats_tcp)
    if not tcp:
        return
    _ts, _r, overflows, drops = tcp[-1]
    if overflows == 0 and drops == 0:
        return
    store.upsert_alert(_alert(
        severity="crit",
        detector="netstats-listen-drops",
        title=f"Listen-queue dropping connections "
              f"(overflows={overflows:.1f}/s drops={drops:.1f}/s)",
        detail=(
            "The kernel is dropping incoming TCP connections "
            "because smartmetd is not accepting fast enough. Every "
            "drop here is a client that got `connection refused` "
            "or a SYN that got no SYN-ACK.\n\n"
            "Likely causes:\n"
            "  - smartmetd is CPU-blocked on something else (the "
            "on-CPU flame will show the stall; runqlat may show "
            "scheduler-side latency).\n"
            "  - Listen backlog is too small — check "
            "net.core.somaxconn and the listen() argument used "
            "by spine.\n"
            "  - A burst-incoming traffic pattern exceeds the "
            "backlog momentarily; consider raising somaxconn.\n\n"
            "This is a definite operational bug whenever it "
            "appears. Always investigate."
        ),
        suggested_panel="f",
        suggested_action="open Flame view; look for stalls in the accept() path",
        docs_anchor="network--tcp-retransmits--listen-drops--nic-bandwidth-proc-panel",
    ))


def detect_perf_record_failed(store) -> None:
    """Surface the most recent perf record failure as an alert so the
    operator notices even if they're not on the Flame panel. perf
    failures are usually a kernel.perf_event_paranoid issue or a
    missing debuginfo install — both cheap to fix once seen.
    """
    err = getattr(store, "perf_last_error", "")
    if not err:
        return
    first = err.splitlines()[0].strip()[:100]
    store.upsert_alert(_alert(
        severity="warn",
        detector="perf-record-failed",
        title=f"perf record failing — {first}",
        detail=(
            "The last perf cycle failed. Common fixes:\n"
            "  - kernel.perf_event_paranoid > 2 — set it to 1 or "
            "run smtop as root.\n"
            "  - perf binary missing — `dnf install perf` (it's a "
            "soft Recommends).\n"
            "  - debuginfo missing — symbols come back as "
            "`[unknown]`; install smartmet-server-debuginfo.\n\n"
            "Full diagnostic in the Flame panel."
        ),
        suggested_panel="f",
        suggested_action="open Flame view for the full diagnostic",
        docs_anchor=None,
    ))
