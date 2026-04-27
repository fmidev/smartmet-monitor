"""Host-wide page-cache and memory-reclaim stats from /proc/vmstat
and /proc/meminfo.

Complements the per-PID major-fault rate already collected from
/proc/PID/stat. Where pgmajfault on a single PID says "this process
hit the disk", host-level reclaim counters say "any allocation
right now may have to wait for the kernel to free memory before it
gets satisfied". Direct reclaim is the canonical hidden-latency
signal: a process called malloc, the page allocator could not find
free pages, and the calling thread had to reclaim some itself
before the allocation returned. None of that shows in CPU
utilisation, in URL p95, or in the on-CPU flamegraph — but it
shows in the wall-clock time of every alloc that runs while it
is happening.

Backend: just /proc, available on every Linux. No bcc-tools, no
perf, no eBPF. Always-on regardless of host config.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

from . import detectors


def _read_proc_vmstat() -> Dict[str, int]:
    """{key: cumulative_value} from /proc/vmstat. All values are
    integers; the file is a flat `key value` per line. Cumulative
    since boot — the loop computes per-second rates from the
    delta between successive samples."""
    out: Dict[str, int] = {}
    try:
        with open("/proc/vmstat", "r") as f:
            for line in f:
                k, _, v = line.partition(" ")
                try:
                    out[k.strip()] = int(v.strip())
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _read_proc_meminfo() -> Dict[str, int]:
    """{name: kilobytes}. Returns 0 for any field the kernel does not
    expose (some virtualised hosts hide MemAvailable on very old
    kernels — defaulting to 0 keeps the panel rendering rather than
    crashing on absence)."""
    out: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                k, _, v = line.partition(":")
                if not v:
                    continue
                parts = v.strip().split()
                try:
                    out[k.strip()] = int(parts[0])
                except (ValueError, IndexError):
                    continue
    except OSError:
        pass
    return out


def _coalesce(d: Dict[str, int], *names: str) -> int:
    """Sum the values of any of `names` that appear in `d`.

    Papers over kernel-version naming differences. The pgsteal /
    pgscan counters were collapsed in 5.x kernels — older kernels
    expose `pgsteal_kswapd_normal` (and per-zone variants), newer
    ones expose `pgsteal_kswapd` directly. Summing every variant
    yields the right total either way.
    """
    return sum(d.get(n, 0) for n in names)


def _rate(now: int, prev: int, dt: float) -> float:
    if dt <= 0:
        return 0.0
    return max(0, now - prev) / dt


async def vmstats_loop(store, interval: float = 5.0) -> None:
    """Sample /proc/vmstat + /proc/meminfo every interval seconds.

    Always-on; same shape as netstats_loop. The first cycle just
    seeds the cumulative baseline so rates are computed from sample
    2 onward. The detector for direct reclaim is invoked at the
    end of each cycle so an alert fires the moment three
    consecutive samples cross threshold.
    """
    store.vmstats_enabled = True
    store.vmstats_status = "sampling /proc/vmstat + /proc/meminfo"
    last_v: Optional[Dict[str, int]] = None
    last_ts: float = 0.0
    while True:
        now = time.time()
        v = _read_proc_vmstat()
        mi = _read_proc_meminfo()
        if last_v is not None and last_ts > 0 and now > last_ts:
            dt = now - last_ts
            majflt_rate = _rate(v.get("pgmajfault", 0),
                                last_v.get("pgmajfault", 0), dt)
            kswapd_now = _coalesce(v, "pgsteal_kswapd",
                                    "pgsteal_kswapd_normal")
            kswapd_prev = _coalesce(last_v, "pgsteal_kswapd",
                                     "pgsteal_kswapd_normal")
            direct_now = _coalesce(v, "pgsteal_direct",
                                    "pgsteal_direct_normal")
            direct_prev = _coalesce(last_v, "pgsteal_direct",
                                     "pgsteal_direct_normal")
            scan_now = _coalesce(v, "pgscan_kswapd",
                                  "pgscan_kswapd_normal",
                                  "pgscan_direct",
                                  "pgscan_direct_normal")
            scan_prev = _coalesce(last_v, "pgscan_kswapd",
                                   "pgscan_kswapd_normal",
                                   "pgscan_direct",
                                   "pgscan_direct_normal")
            kswapd_rate = _rate(kswapd_now, kswapd_prev, dt)
            direct_rate = _rate(direct_now, direct_prev, dt)
            scan_rate = _rate(scan_now, scan_prev, dt)
            cache_kb = mi.get("Cached", 0) + mi.get("Buffers", 0)
            mem_total_kb = mi.get("MemTotal", 0) or 1
            mem_avail_kb = mi.get("MemAvailable", 0)
            store.vmstats_record(
                ts=now,
                majflt_rate=majflt_rate,
                kswapd_rate=kswapd_rate,
                direct_rate=direct_rate,
                scan_rate=scan_rate,
                cache_kb=cache_kb,
                mem_total_kb=mem_total_kb,
                mem_avail_kb=mem_avail_kb,
            )
            detectors.detect_vmstats_direct_reclaim(store)
        last_v = v
        last_ts = now
        await asyncio.sleep(interval)
