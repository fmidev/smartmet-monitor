"""Runtime capability probes for kernel-profiling backends.

smartmet-monitor supports kernel profiling via two backends:

  * `perf` (linux-tools)            — already used by perftop.py
  * `bcc-tools` / `bpftrace` (eBPF) — used by off-CPU and lock-wait
                                       paths added later

Both ship on every supported distribution, but the userspace tools
may not be installed: RHEL 8 puts perf in `linux-tools`, bcc in
`bcc-tools`, and bpftrace in `bpftrace`. This module asks the
filesystem and PATH whether a given capability is actually
available *right now* — none of these calls execute the underlying
tool, so probing is cheap and side-effect-free.

Each probe returns a (bool, reason) tuple. The reason is the human
text the panel surfaces when the capability is missing, so the
operator can fix the install instead of guessing why a feature
went silent.

Results are cached per-process: the kernel does not gain or lose
capabilities while smtop is running, and the user does not install
new packages without restarting smtop.
"""

from __future__ import annotations

import os
import platform
import shutil
from functools import lru_cache
from typing import Tuple


CapResult = Tuple[bool, str]


@lru_cache(maxsize=1)
def kernel_release() -> str:
    """e.g. '4.18.0' on RHEL 8, '6.x' on Fedora."""
    return platform.release().split("-", 1)[0]


@lru_cache(maxsize=1)
def kernel_at_least(major: int, minor: int = 0) -> bool:
    parts = kernel_release().split(".")
    try:
        ki = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except ValueError:
        return False
    return ki >= (major, minor)


def _tracepoint_exists(name: str) -> bool:
    """name = 'sched/sched_switch' etc. Probe both classic and modern paths."""
    return any(
        os.path.exists(f"{base}/events/{name}/id")
        for base in ("/sys/kernel/debug/tracing", "/sys/kernel/tracing")
    )


@lru_cache(maxsize=1)
def have_perf() -> CapResult:
    if shutil.which("perf"):
        return True, "perf in PATH"
    return False, "perf not in PATH (dnf install perf)"


@lru_cache(maxsize=1)
def have_perf_offcpu() -> CapResult:
    """sched:sched_switch tracepoint + perf record can capture stacks.

    This is the pure-perf fallback path. It works on every RHEL 8 host
    that already has the on-CPU flamegraph working — perf 4.18 supports
    --call-graph=dwarf and the sched tracepoints have been stable since
    2.6.x.
    """
    ok, reason = have_perf()
    if not ok:
        return False, reason
    if not _tracepoint_exists("sched/sched_switch"):
        return False, "sched:sched_switch tracepoint missing (kernel too old?)"
    return True, "perf + sched_switch tracepoint"


@lru_cache(maxsize=1)
def have_offcputime_bcc() -> CapResult:
    """bcc-tools' offcputime command (preferred over the perf path)."""
    for binary in ("offcputime-bpfcc", "offcputime"):
        path = shutil.which(binary)
        if path:
            return True, path
    return False, ("offcputime not in PATH "
                   "(dnf install bcc-tools)")


@lru_cache(maxsize=1)
def have_biolatency_bcc() -> CapResult:
    for binary in ("biolatency-bpfcc", "biolatency"):
        path = shutil.which(binary)
        if path:
            return True, path
    return False, "biolatency not in PATH (dnf install bcc-tools)"


@lru_cache(maxsize=1)
def have_bpftrace() -> CapResult:
    path = shutil.which("bpftrace")
    if path:
        return True, path
    return False, "bpftrace not in PATH (dnf install bpftrace)"


def offcpu_backend() -> Tuple[str, str]:
    """Pick a backend for off-CPU profiling.

    Returns (kind, detail) where kind is one of:

      * 'bcc'  — use the offcputime-bpfcc path (low overhead, preferred)
      * 'perf' — pure-perf sched:sched_switch fallback
      * ''     — neither available; detail is the install hint to show

    The picker prefers bcc because its eBPF-based aggregation has a
    fraction of the overhead of recording every sched_switch via perf.
    """
    ok, info = have_offcputime_bcc()
    if ok:
        return "bcc", info
    ok, info = have_perf_offcpu()
    if ok:
        return "perf", info
    bcc_ok, bcc_msg = have_offcputime_bcc()
    perf_ok, perf_msg = have_perf_offcpu()
    return "", f"{bcc_msg}; or {perf_msg}"
