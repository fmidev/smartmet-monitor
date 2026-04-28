"""Proc snapshot — smartmetd processes from /proc."""

from __future__ import annotations

from ..state.store import ProcSample


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
