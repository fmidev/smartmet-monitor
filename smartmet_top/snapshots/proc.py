"""Proc snapshot — smartmetd processes from /proc."""

from __future__ import annotations

from typing import List, Optional

from ..state.store import ProcSample


def _rate(samples, attr: str) -> List[float]:
    """delta(samples[i].attr) / delta(ts) — matches the panel's rate
    computation. First sample yields 0.0 (no delta available)."""
    if not samples or len(samples) < 2:
        return [0.0] * len(samples)
    out = [0.0]
    for i in range(1, len(samples)):
        s0, s1 = samples[i - 1], samples[i]
        dt = max(1e-6, s1.ts - s0.ts)
        out.append(max(0.0, (getattr(s1, attr) - getattr(s0, attr)) / dt))
    return out


class ProcSnapshot:
    name = "proc"

    @staticmethod
    def table(store):
        headers = [
            "pid", "role", "cmdline",
            "vm_rss_kb", "rss_anon_kb", "rss_file_kb", "rss_shmem_kb",
            "vm_size_kb", "vm_swap_kb", "vm_pte_kb", "vm_hwm_kb",
            "threads", "fds",
            "io_read_bytes", "io_write_bytes",
        ]
        rows = []
        for info in store.proc_list():
            s = info.samples[-1] if info.samples else ProcSample()
            rows.append([
                info.pid, info.role, info.cmdline,
                s.vm_rss_kb, s.rss_anon_kb, s.rss_file_kb, s.rss_shmem_kb,
                s.vm_size_kb, s.vm_swap_kb, s.vm_pte_kb, s.vm_hwm_kb,
                s.threads, s.fds,
                s.io_read_bytes, s.io_write_bytes,
            ])
        return headers, rows

    @staticmethod
    def detail(store, pid: Optional[int] = None) -> dict:
        """Per-PID detail for the Proc panel: latest counters plus
        time-series of RSS, IO read/write rate, page-fault rate.

        ``pid`` defaults to ``store.proc_default_pid()``.
        """
        if pid is None:
            pid = store.proc_default_pid()
        info = next(
            (p for p in store.proc_list() if p.pid == pid),
            None,
        )
        if info is None:
            return {"pid": pid, "found": False}

        samples = list(info.samples)
        latest = samples[-1] if samples else ProcSample()
        ts = [round(s.ts, 3) for s in samples]
        return {
            "pid": info.pid,
            "role": info.role,
            "cmdline": info.cmdline,
            "found": True,
            "latest": {
                "vm_rss_kb": latest.vm_rss_kb,
                "rss_anon_kb": latest.rss_anon_kb,
                "rss_file_kb": latest.rss_file_kb,
                "rss_shmem_kb": latest.rss_shmem_kb,
                "vm_size_kb": latest.vm_size_kb,
                "vm_swap_kb": latest.vm_swap_kb,
                "vm_hwm_kb": latest.vm_hwm_kb,
                "threads": latest.threads,
                "fds": latest.fds,
                "io_read_bytes": latest.io_read_bytes,
                "io_write_bytes": latest.io_write_bytes,
            },
            "series": {
                "ts": ts,
                "vm_rss_kb": [s.vm_rss_kb for s in samples],
                "rss_anon_kb": [s.rss_anon_kb for s in samples],
                "rss_file_kb": [s.rss_file_kb for s in samples],
                "io_read_bps": [round(v, 1)
                                for v in _rate(samples, "io_read_bytes")],
                "io_write_bps": [round(v, 1)
                                 for v in _rate(samples, "io_write_bytes")],
                "majflt_per_s": [round(v, 3)
                                 for v in _rate(samples, "majflt")],
                "threads": [s.threads for s in samples],
                "fds": [s.fds for s in samples],
            },
        }

    @staticmethod
    def list_pids(store) -> List[dict]:
        return [
            {"pid": p.pid, "role": p.role, "cmdline": p.cmdline}
            for p in store.proc_list()
        ]
