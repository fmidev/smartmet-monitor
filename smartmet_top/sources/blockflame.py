"""Block-I/O issue stack sampler — where in code does smartmetd issue I/O.

Wraps `perf record -e block:block_rq_issue -ag -p PID -- sleep N`
and parses the resulting stacks. The Flame view's block-I/O mode
renders the tree, weighted by I/O count: each sample is one block
operation issued to the device, so frame width measures "this code
path issued N block requests".

Why this exists. The Proc panel's Block I/O latency sparkline
already alerts when the disk is saturated. Page faults catch
synchronous reads serviced through the page-cache miss path.
This view catches *every* block I/O — direct reads, writes,
fsyncs, the whole stream. Pairs with biolatency the way the
page-fault flame pairs with the page-fault rate: when latency
spikes, this names which code path is queueing the work.

Backend: pure perf, no bcc dependency. The block:block_rq_issue
tracepoint is the standard "block layer issue" event used in
Brendan Gregg's biolat / iolat / disksnoop scripts and has been
stable since 2.6.x.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import Optional, Tuple

from .perftop import parse_perf_script


PERF_DATA_PATH = "/tmp/.smtop_perf_blockio.data"


async def _run_perf_record(perf_bin: str, pid: int,
                           record_seconds: int) -> Tuple[bool, int, str]:
    cmd = [
        perf_bin, "record",
        "-e", "block:block_rq_issue",
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


async def blockflame_loop(store, interval: float = 10.0,
                          record_seconds: int = 5) -> None:
    perf_bin = shutil.which("perf")
    if not perf_bin:
        store.blockflame_status = "perf not found in PATH"
        store.blockflame_enabled = False
        return
    store.blockflame_enabled = True
    store.blockflame_status = "waiting for PID"
    rs = max(1, int(record_seconds))
    while True:
        pid: Optional[int] = store.proc_selected()
        if pid is None:
            await asyncio.sleep(0.5)
            continue
        store.blockflame_status = f"recording pid={pid} ({rs}s)"
        try:
            ok, rc, diag = await _run_perf_record(perf_bin, pid, rs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.blockflame_status = f"record error: {e}"
            store.blockflame_last_error = str(e)
            await asyncio.sleep(interval)
            continue
        if not ok:
            first = (diag.splitlines() or [""])[0].strip()[:120]
            store.blockflame_status = f"record failed (exit={rc}): {first}"
            store.blockflame_last_error = (
                diag or f"perf exited with {rc} and no diagnostic"
            )
            await asyncio.sleep(interval)
            continue
        store.blockflame_last_error = ""
        try:
            text = await _run_perf_script(perf_bin)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.blockflame_status = f"script error: {e}"
            await asyncio.sleep(interval)
            continue
        stacks = parse_perf_script(text)
        store.blockflame_record_samples(pid, time.time(), stacks)
        store.blockflame_status = f"ok pid={pid} samples={len(stacks)}"
        target = time.time() + max(0.0, interval - rs)
        while time.time() < target:
            if store.proc_selected() != pid:
                break
            await asyncio.sleep(0.5)
