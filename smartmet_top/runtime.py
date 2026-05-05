"""Source-task lifecycle, shared between smtop and smwebmon.

Both binaries need the same data-collection task graph: log tail,
admin poll, ``/proc``, ``/proc/net``, ``/proc/vmstat``, journal,
plus optional perf samplers gated on ``--perf``. The curses
``smtop`` runs them alongside its key+draw loop; ``smwebmon`` runs
them alongside an HTTP server. Keeping the wiring in one place
means the two binaries cannot drift on which sources are scheduled
or how.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple

from .sources.adminapi import poll_all
from .sources.biolat import biolat_loop
from .sources.blockflame import blockflame_loop
from .sources.journal import journal_loop
from .sources.logtail import bulk_load, tail_many
from .sources.mallocflame import mallocflame_loop
from .sources.netstats import netstats_loop
from .sources.offcpu import offcpu_loop
from .sources.pagefault import pagefault_loop
from .sources.perfstat import perfstat_loop
from .sources.perftop import perf_loop
from .sources.proc import proc_loop
from .sources.runqlat import runqlat_loop
from .sources.vmstats import vmstats_loop
from .sources.wakeup import wakeup_loop
from .state.store import Store


async def replay_logs(store: Store, log_paths: List[str], *,
                      replay_bytes: int = 1024 * 1024 * 1024,
                      include_rotated: bool = False) -> None:
    """Synchronous bulk-load of log tails. Run before ``start_sources``
    so panels come up populated rather than empty.

    Maintains ``store.replay_status`` as a small dict the dashboard
    polls via ``/api/health`` so it can show a "processing logs…"
    banner instead of leaving the operator staring at empty panels
    while a multi-GB replay parses.
    """
    if not log_paths:
        return
    import time as _time
    started = _time.time()
    store.replay_status = {
        "in_progress": True,
        "files_total": len(log_paths),
        "include_rotated": bool(include_rotated),
        "started_at": started,
    }
    try:
        await bulk_load(log_paths, store,
                        max_bytes_per_file=replay_bytes,
                        include_rotated=include_rotated)
    finally:
        store.replay_status = {
            "in_progress": False,
            "files_total": len(log_paths),
            "started_at": started,
            "duration_seconds": _time.time() - started,
        }


def start_sources(store: Store, *,
                  log_paths: Optional[List[str]] = None,
                  admin_urls: Optional[List[Tuple[str, str]]] = None,
                  admin_interval: float = 2.0,
                  enable_perf: bool = False,
                  perf_interval: float = 10.0,
                  perf_record_seconds: int = 3,
                  malloc_flame_min_bytes: Optional[int] = None,
                  journal_unit: str = "smartmet-backend,smartmet-frontend",
                  ) -> List[asyncio.Task]:
    """Schedule the always-on and opt-in source tasks. Returns the list
    of created tasks so the caller can cancel/await them.

    Always-on tasks: ``proc_loop``, ``netstats_loop``, ``vmstats_loop``.
    Plus ``tail_many`` if ``log_paths`` is non-empty, ``poll_all`` if
    ``admin_urls`` is non-empty, ``journal_loop`` if ``journal_unit`` is
    a non-empty string. ``journal_unit`` may be comma-separated to
    follow multiple systemd units in one merged stream — this is the
    default ("smartmet-backend,smartmet-frontend") so a host that runs
    both daemons is covered without operator intervention.

    Opt-in (gated on ``enable_perf``): on-CPU/off-CPU/page-fault/
    wakeup/block flame samplers, biolat, runqlat, perfstat. The
    allocation flame is further gated on a non-None
    ``malloc_flame_min_bytes``.

    Idempotent w.r.t. ``register_admin_host``; safe to call after the
    caller has already registered hosts.
    """
    log_paths = log_paths or []
    admin_urls = admin_urls or []
    for host, _ in admin_urls:
        store.register_admin_host(host)
    store.perf_enabled = enable_perf

    tasks: List[asyncio.Task] = []
    if log_paths:
        tasks.append(asyncio.create_task(tail_many(log_paths, store)))
    if admin_urls:
        tasks.append(asyncio.create_task(
            poll_all(admin_urls, store, admin_interval)))
    # Always-on counters from /proc — work without log files or admin
    # URLs as long as smartmetd is running on this host.
    tasks.append(asyncio.create_task(proc_loop(store)))
    tasks.append(asyncio.create_task(netstats_loop(store)))
    tasks.append(asyncio.create_task(vmstats_loop(store)))
    if journal_unit:
        tasks.append(asyncio.create_task(journal_loop(store, journal_unit)))
    if enable_perf:
        tasks.append(asyncio.create_task(
            perf_loop(store, perf_interval, perf_record_seconds)))
        # Off-CPU sampler runs alongside the on-CPU perf sampler so the
        # Flame view's `o` toggle has data to switch into. The loop
        # probes its backend internally and exits cleanly with an
        # install hint in offcpu_status if neither bcc-tools nor the
        # perf fallback is available — no overhead in that case.
        tasks.append(asyncio.create_task(
            offcpu_loop(store, perf_interval, perf_record_seconds)))
        tasks.append(asyncio.create_task(
            pagefault_loop(store, perf_interval, perf_record_seconds)))
        tasks.append(asyncio.create_task(
            wakeup_loop(store, perf_interval, perf_record_seconds)))
        tasks.append(asyncio.create_task(
            blockflame_loop(store, perf_interval, perf_record_seconds)))
        if malloc_flame_min_bytes is not None:
            tasks.append(asyncio.create_task(
                mallocflame_loop(store, min_bytes=malloc_flame_min_bytes)))
        tasks.append(asyncio.create_task(biolat_loop(store)))
        tasks.append(asyncio.create_task(runqlat_loop(store)))
        tasks.append(asyncio.create_task(
            perfstat_loop(store, perf_interval, perf_record_seconds)))
    return tasks
