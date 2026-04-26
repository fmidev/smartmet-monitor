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
from typing import Optional, Tuple


CapResult = Tuple[bool, str]


# Where bcc-tools binaries live across the distros we support, in
# preferred order. shutil.which() only walks $PATH; on RHEL 8
# `/usr/share/bcc/tools/` (the package's actual script directory)
# is usually not in PATH, and `/usr/sbin/` is dropped by sudo's
# default `secure_path`. Probing these directories directly catches
# both cases without making the operator export PATH.
_BCC_SEARCH_DIRS = (
    "/usr/sbin",                # Fedora / RHEL 9+ / RHEL 8 wrappers
    "/usr/share/bcc/tools",     # RHEL 8 default, Python scripts directly
    "/usr/share/bpfcc-tools",   # Debian / Ubuntu (bpfcc-tools package)
    "/usr/local/sbin",
    "/usr/local/share/bcc/tools",
)


def _find_bcc_tool(*candidates: str) -> Optional[str]:
    """Find a bcc-tools binary by name, falling back from $PATH to the
    well-known install directories. Returns the absolute path when
    found, None otherwise. Each candidate is tried in turn — typically
    `("offcputime-bpfcc", "offcputime")` so the suffixed wrapper wins
    if both exist.
    """
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
        for base in _BCC_SEARCH_DIRS:
            full = os.path.join(base, name)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                return full
    return None


def _bcc_install_hint(tool: str) -> str:
    """Helpful, distro-aware not-found message that lists where we
    looked. Shown verbatim in the panel install hint, so it should
    name the package the operator can install."""
    where = ", ".join(_BCC_SEARCH_DIRS)
    return (f"{tool} not found in $PATH or {where} "
            f"— install bcc-tools (RHEL/Fedora: dnf install bcc-tools; "
            f"Debian/Ubuntu: apt install bpfcc-tools)")


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
    """bcc-tools' offcputime command (preferred over the perf path).

    Looks in $PATH and the canonical bcc install directories — see
    `_find_bcc_tool` for the rationale (RHEL 8 puts the script in
    /usr/share/bcc/tools/, sudo's secure_path often omits /usr/sbin).
    """
    path = _find_bcc_tool("offcputime-bpfcc", "offcputime")
    if path:
        return True, path
    return False, _bcc_install_hint("offcputime")


@lru_cache(maxsize=1)
def have_biolatency_bcc() -> CapResult:
    path = _find_bcc_tool("biolatency-bpfcc", "biolatency")
    if path:
        return True, path
    return False, _bcc_install_hint("biolatency")


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
