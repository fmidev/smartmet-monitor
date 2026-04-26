"""Perf-top sampler for the focused smartmetd PID.

Spawns `perf record -g -F 99 -p PID -- sleep N` periodically, then parses
`perf script` output to extract leaf-symbol counts and full call-stack
samples. Both are pushed into the Store; the ProcPanel renders them as
"top symbols" and "flamegraph" views.

This is opt-in (`--perf`) because perf adds real load to the target
process. The duty cycle (record 1s, idle until interval seconds elapse)
keeps overhead bounded and predictable.

Symbols depend on debug info; FMI installs the smartmet-server-debuginfo
package on production hosts, so we expect real names rather than
`[unknown]`.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from typing import List, Optional, Tuple


PERF_FREQ = 99                  # samples/sec, mirrors btop's CPU graph default
DEFAULT_RECORD_SECONDS = 3      # actual recording duration each cycle
DEFAULT_INTERVAL = 10.0         # full cycle (record + idle)
PERF_DATA_PATH = "/tmp/.smtop_perf.data"


_FRAME_RE = re.compile(
    r"^\s+[0-9a-fA-F]+\s+(?P<sym>.+?)\s+\((?P<dso>[^()]*)\)\s*$"
)
_OFFSET_RE = re.compile(r"\+0x[0-9a-fA-F]+$")


def _strip_offset(symbol: str) -> str:
    return _OFFSET_RE.sub("", symbol).strip() or "[unknown]"


def parse_perf_script(text: str) -> List[Tuple[str, ...]]:
    """Parse `perf script` output into a list of stacks.

    Each returned stack is a tuple of symbols ordered root → leaf
    (outermost call first), matching the conventional flamegraph layout.
    perf-script emits frames in leaf → root order, so we reverse here.
    Blank lines separate samples.
    """
    stacks: List[Tuple[str, ...]] = []
    current: List[str] = []
    in_sample = False
    for raw in text.splitlines():
        if not raw.strip():
            if current:
                stacks.append(tuple(reversed(current)))
                current = []
            in_sample = False
            continue
        if not in_sample:
            in_sample = True
            continue
        m = _FRAME_RE.match(raw)
        if not m:
            continue
        sym = _strip_offset(m.group("sym"))
        current.append(sym)
    if current:
        stacks.append(tuple(reversed(current)))
    return stacks


async def _run_perf_record(perf_bin: str, pid: int,
                           record_seconds: int) -> Tuple[bool, int, str]:
    """Run perf record; return (ok, returncode, combined_diagnostic_text).

    Notes on the command line:
      * `--call-graph=dwarf,32768` rather than the default `-g` (which
        is `--call-graph=fp`). SmartMet Server has deep call stacks
        that the frame-pointer mode splits — partial stacks show up
        in the flamegraph as separate trees rooted somewhere in the
        middle of the call hierarchy. DWARF unwinding reconstructs
        the full chain reliably. The 32 KB stack-dump size (default
        is 8 KB) is sized for those deep stacks; perf truncates
        anything taller. perf.data files grow significantly under
        DWARF mode — proportional to sample count × stack-dump
        size — but the recording cycle is bounded and the same
        `/tmp/.smtop_perf.data` file is overwritten each cycle.
      * `-q` is deliberately omitted — it silences perf's own progress
        and error messages, which is what the operator actually needs
        when sampling fails.
      * `--no-children` is a `perf report` aggregation flag, not a
        valid `perf record` option in many builds. It's harmless on
        builds that accept it but raises "unknown option" on others
        (notably some RHEL 8 perf builds), which manifested as a bare
        "record failed" with no useful diagnostic.
    """
    cmd = [
        perf_bin, "record",
        "-F", str(PERF_FREQ),
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
    # Combine stdout + stderr — perf normally writes diagnostics to
    # stderr but some builds emit warnings on stdout, and we'd rather
    # surface both than silently drop one.
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


async def perf_loop(store, interval: float = DEFAULT_INTERVAL,
                    record_seconds: int = DEFAULT_RECORD_SECONDS) -> None:
    perf_bin = shutil.which("perf")
    if not perf_bin:
        store.perf_status = "perf not found in PATH (install linux-tools)"
        store.perf_enabled = False
        return
    # Seed the store with the CLI default; the Flame view's selection
    # overlay can mutate this and we'll pick the new value up on the
    # next iteration without restarting smtop.
    store.perf_record_seconds = record_seconds
    store.perf_status = "waiting for PID"
    while True:
        pid: Optional[int] = store.proc_selected()
        if pid is None:
            await asyncio.sleep(0.5)
            continue
        rs = max(1, int(getattr(store, "perf_record_seconds", record_seconds)))
        store.perf_target_pid = pid
        store.perf_status = f"recording pid={pid} ({rs}s)"
        try:
            ok, rc, diag = await _run_perf_record(perf_bin, pid, rs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.perf_status = f"record error: {e}"
            store.perf_last_error = str(e)
            await asyncio.sleep(interval)
            continue
        if not ok:
            # Keep the short summary panel-header-friendly; stash the full
            # diagnostic so the panels can show it as multi-line context.
            first = (diag.splitlines() or [""])[0].strip()
            short = first[:120] if first else f"exit={rc}"
            store.perf_status = f"record failed (exit={rc}): {short}"
            store.perf_last_error = diag or f"perf record exited with {rc} and no diagnostic output"
            await asyncio.sleep(interval)
            continue
        # Success — clear any previous error so the panel stops showing it.
        store.perf_last_error = ""
        store.perf_status = f"parsing pid={pid}"
        try:
            text = await _run_perf_script(perf_bin)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.perf_status = f"script error: {e}"
            await asyncio.sleep(interval)
            continue
        stacks = parse_perf_script(text)
        store.perf_record_samples(pid, time.time(), stacks)
        store.perf_status = f"ok pid={pid} samples={len(stacks)}"
        # Idle remainder of the duty cycle, but break early if the user
        # selects a different PID so the next cycle re-targets quickly.
        target = time.time() + max(0.0, interval - rs)
        while time.time() < target:
            if store.proc_selected() != pid:
                break
            await asyncio.sleep(0.5)
