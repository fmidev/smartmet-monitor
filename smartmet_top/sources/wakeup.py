"""Wakeup stack sampler — who is unblocking smartmetd's threads.

Wraps `perf record -e sched:sched_wakeup -ag -p PID -- sleep N` and
parses the resulting stacks. The Flame view's wakeup mode renders
the tree, weighted by wakeup count: each sample is one wakeup
event, so frame width measures "this code path generated N
wakeups for the focused PID".

Why this exists. The off-CPU view shows where threads are blocked.
The wakeup view shows the *complementary* picture: who is doing
the unblocking. Together they identify the lock-holder side of
contention. The standard Brendan Gregg recipe — see
brendangregg.com/FlameGraphs/offcpuflamegraphs.html — is to walk
from a tall narrow stack in the off-CPU flame to its dual in the
wakeup flame: same lock, opposite side of the wait.

Backend: pure perf, no bcc dependency, RHEL 8 native. The
sched:sched_wakeup tracepoint has been stable since the 2.6 days.

Note on what `-p PID` captures. perf attaches the event to the
*PID's* context, which means the recorded events are wakeups
*initiated by* the focused PID — the threads it is unblocking. To
see who is waking it up the operator has to record host-wide and
filter by the wakee field; we do not do that here because most of
the operationally interesting cases on a multi-threaded server
boil down to "smartmetd's worker pool waking each other" which is
exactly what `-p PID` captures.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import Optional, Tuple

from .perftop import parse_perf_script


PERF_DATA_PATH = "/tmp/.smtop_perf_wakeup.data"


async def _run_perf_record(perf_bin: str, pid: int,
                           record_seconds: int) -> Tuple[bool, int, str]:
    cmd = [
        perf_bin, "record",
        "-e", "sched:sched_wakeup",
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


async def wakeup_loop(store, interval: float = 10.0,
                      record_seconds: int = 5) -> None:
    perf_bin = shutil.which("perf")
    if not perf_bin:
        store.wakeup_status = "perf not found in PATH"
        store.wakeup_enabled = False
        return
    store.wakeup_enabled = True
    store.wakeup_status = "waiting for PID"
    rs = max(1, int(record_seconds))
    while True:
        if getattr(store, "profile_paused", False):
            await asyncio.sleep(0.5)
            continue
        pid: Optional[int] = store.proc_selected()
        if pid is None:
            await asyncio.sleep(0.5)
            continue
        store.wakeup_status = f"recording pid={pid} ({rs}s)"
        try:
            ok, rc, diag = await _run_perf_record(perf_bin, pid, rs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.wakeup_status = f"record error: {e}"
            store.wakeup_last_error = str(e)
            await asyncio.sleep(interval)
            continue
        if not ok:
            first = (diag.splitlines() or [""])[0].strip()[:120]
            store.wakeup_status = f"record failed (exit={rc}): {first}"
            store.wakeup_last_error = (
                diag or f"perf exited with {rc} and no diagnostic"
            )
            await asyncio.sleep(interval)
            continue
        store.wakeup_last_error = ""
        try:
            text = await _run_perf_script(perf_bin)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.wakeup_status = f"script error: {e}"
            await asyncio.sleep(interval)
            continue
        stacks = parse_perf_script(text)
        store.wakeup_record_samples(pid, time.time(), stacks)
        store.wakeup_status = f"ok pid={pid} samples={len(stacks)}"
        target = time.time() + max(0.0, interval - rs)
        while time.time() < target:
            if store.proc_selected() != pid:
                break
            await asyncio.sleep(0.5)
