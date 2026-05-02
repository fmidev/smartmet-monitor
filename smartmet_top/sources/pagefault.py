"""Page-fault stack sampler — where in the code base cold pages get touched.

Wraps `perf record -e major-faults -c 1 -ag -p PID -- sleep N` and
parses each sample's stack into the Store. The Flame view's third
mode (after on-CPU and off-CPU) renders the resulting tree, weighted
by sample count: each sample is one major page fault, so the flame
shows "this function caused N synchronous reads from disk" by frame
width.

Why this exists. The Proc panel's page-fault rate sparkline already
fires an alert when a storm starts. The next question — "which code
path is hitting cold pages?" — needs stacks. Without them, the
operator can see *that* a fault storm is happening but not *where*,
and the on-CPU flame is no help (the kernel work is mostly waiting,
not running). This sampler closes that gap.

Backend: pure perf. No bcc-tools, no eBPF, runs anywhere the
existing on-CPU sampler does. The major-faults event is a kernel
software event that has been stable since 2.6.x; available on every
distro we support including RHEL 8.

Sampling strategy. Unlike on-CPU which samples at 99 Hz to bound
overhead, page faults are event-driven — `perf record -c 1` captures
every single fault. Major faults are inherently low-frequency
(synchronous block I/O caps the rate at the disk's IOPS), so even
the worst storm produces sample volumes the parser can handle.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import Optional, Tuple

from .perftop import parse_perf_script


PERF_DATA_PATH = "/tmp/.smtop_perf_pagefault.data"


async def _run_perf_record(perf_bin: str, pid: int,
                           record_seconds: int) -> Tuple[bool, int, str]:
    """Record stacks at every major-fault for this PID for N seconds."""
    cmd = [
        perf_bin, "record",
        "-e", "major-faults",
        "-c", "1",
        "--call-graph=dwarf,32768",
        "-p", str(pid),
        "-o", PERF_DATA_PATH,
        "--", "sleep", str(record_seconds),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1
    text = (stderr.decode("utf-8", errors="replace") + "\n"
            + stdout.decode("utf-8", errors="replace")).strip()
    return rc == 0, rc, text


async def _run_perf_script(perf_bin: str) -> str:
    cmd = [perf_bin, "script", "-i", PERF_DATA_PATH, "--no-inline"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return out.decode("utf-8", errors="replace")


async def pagefault_loop(store, interval: float = 10.0,
                         record_seconds: int = 5) -> None:
    """Cycle: pick the focused PID, record major-fault stacks for
    `record_seconds`, push to the Store. Idle for the remainder of
    `interval`. Same shape as perftop's loop so the duty-cycle
    budget is predictable.

    Default record_seconds is longer than the on-CPU sampler's 3 s
    because major faults are sparse — at typical SmartMet steady
    state you might see only a handful per second, and a 5 s window
    gives a more useful flame.
    """
    perf_bin = shutil.which("perf")
    if not perf_bin:
        store.pagefault_status = "perf not found in PATH"
        store.pagefault_enabled = False
        return
    store.pagefault_enabled = True
    store.pagefault_status = "waiting for PID"
    rs = max(1, int(record_seconds))
    while True:
        if getattr(store, "profile_paused", False):
            await asyncio.sleep(0.5)
            continue
        pid: Optional[int] = store.proc_selected()
        if pid is None:
            await asyncio.sleep(0.5)
            continue
        store.pagefault_status = f"recording pid={pid} ({rs}s)"
        try:
            ok, rc, diag = await _run_perf_record(perf_bin, pid, rs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.pagefault_status = f"record error: {e}"
            store.pagefault_last_error = str(e)
            await asyncio.sleep(interval)
            continue
        if not ok:
            first = (diag.splitlines() or [""])[0].strip()[:120]
            store.pagefault_status = f"record failed (exit={rc}): {first}"
            store.pagefault_last_error = (
                diag or f"perf exited with {rc} and no diagnostic"
            )
            await asyncio.sleep(interval)
            continue
        store.pagefault_last_error = ""
        try:
            text = await _run_perf_script(perf_bin)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.pagefault_status = f"script error: {e}"
            await asyncio.sleep(interval)
            continue
        stacks = parse_perf_script(text)
        store.pagefault_record_samples(pid, time.time(), stacks)
        store.pagefault_status = (
            f"ok pid={pid} samples={len(stacks)}"
        )
        target = time.time() + max(0.0, interval - rs)
        while time.time() < target:
            if store.proc_selected() != pid:
                break
            await asyncio.sleep(0.5)
