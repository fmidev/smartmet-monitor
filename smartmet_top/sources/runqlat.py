"""Run-queue latency sampler — how long threads sit ready before running.

`runqlat-bpfcc` instruments the kernel scheduler at `sched:sched_wakeup`
and `sched:sched_switch` and produces a power-of-2 histogram of the
delay between "thread became runnable" and "thread actually got CPU".
This is the canonical metric for *scheduler*-side latency: the CPU
counter shows utilisation but says nothing about whether ready
threads got scheduled in a timely manner.

This sampler is operationally most valuable on virtualised hosts
where CFS bandwidth controls or noisy-neighbour VMs can hold
threads off the CPU for tens of milliseconds while utilisation
looks innocuous. On a dedicated bare-metal SmartMet host it
should sit near zero; the moment it climbs, the operator has a
definitive answer to "why is everything slow when the CPU isn't
busy?".

Output format is identical to biolatency's:

         usecs               : count     distribution
             0 -> 1          : 0
             2 -> 3          : 12
             4 -> 7          : 234
             ...

so we reuse the bucket parser and percentile math from
sources/biolat.py rather than duplicating the regex.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from functools import lru_cache
from typing import Optional, Tuple

from . import detectors, profile_caps
from .biolat import parse_biolatency, percentiles_us


@lru_cache(maxsize=1)
def have_runqlat_bcc() -> Tuple[bool, str]:
    for binary in ("runqlat-bpfcc", "runqlat"):
        path = shutil.which(binary)
        if path:
            return True, path
    return False, "runqlat not in PATH (dnf install bcc-tools)"


async def _run_runqlat(binary: str, window_seconds: int
                       ) -> Tuple[bool, int, str]:
    cmd = [binary, str(window_seconds), "1"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if rc != 0:
        return False, rc, (err + "\n" + out).strip()
    return True, 0, out


async def runqlat_loop(store, window_seconds: float = 5.0) -> None:
    """Cycle: run runqlat for `window_seconds`, record one sample.

    Mirrors biolat_loop's shape exactly. The bcc tool blocks for the
    measurement window so the loop self-paces — no extra sleep.
    """
    ok, info = have_runqlat_bcc()
    if not ok:
        store.runqlat_status = info
        store.runqlat_enabled = False
        return
    binary: Optional[str] = (shutil.which("runqlat-bpfcc")
                              or shutil.which("runqlat"))
    if not binary:
        store.runqlat_status = "runqlat disappeared from PATH"
        store.runqlat_enabled = False
        return
    store.runqlat_enabled = True
    store.runqlat_status = f"running ({binary})"
    win_int = max(1, int(window_seconds))
    while True:
        try:
            success, rc, output = await _run_runqlat(binary, win_int)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.runqlat_status = f"run error: {e}"
            store.runqlat_last_error = str(e)
            await asyncio.sleep(win_int)
            continue
        if not success:
            first = (output.splitlines() or [""])[0].strip()[:120]
            store.runqlat_status = f"failed (exit={rc}): {first}"
            store.runqlat_last_error = output
            await asyncio.sleep(win_int)
            continue
        store.runqlat_last_error = ""
        buckets, unit = parse_biolatency(output)
        p50, p95, p99, total = percentiles_us(buckets, unit)
        store.runqlat_record_sample(time.time(), p50, p95, p99, total)
        detectors.detect_runqlat_stalls(store)
        store.runqlat_status = f"ok p95={p95}us p99={p99}us"
