"""Hardware-counter sampler — IPC and cache-miss rate per smartmetd PID.

Wraps `perf stat -e cycles,instructions,cache-references,cache-misses,
branch-misses -p PID -- sleep N` and parses the counter values out of
the human-readable summary perf prints on stderr. Two derived ratios
matter:

  IPC               = instructions / cycles
  cache-miss-rate   = cache-misses / cache-references
  branch-miss-rate  = branch-misses / cache-references — actually
                      branch-misses / instructions, the conventional
                      denominator

A single IPC number captures more about CPU efficiency than any flame
view: at 0.5 IPC the CPU is twiddling its thumbs waiting on memory or
cross-core synchronisation; at 2.0+ IPC the work is well-cached and
nicely parallel inside one core. SmartMet workloads typically run
between 0.4 and 1.0 — anything below 0.3 sustained suggests a hot
loop with bad cache locality (or the host being CPU-stolen, which
runqlat will independently flag).

This source does not depend on bcc-tools — pure perf, available
wherever `--perf` already works. Some virtualised hosts expose only
software PMU events; the parser tolerates `<not supported>` /
`<not counted>` lines and reports them as 0 with a status string.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from typing import Dict, Optional, Tuple

from . import detectors, profile_caps


# perf stat formats numbers with locale-dependent separators (commas
# on en_US, dots/spaces on others). The Python locale isn't reliable
# for parsing perf output; strip everything that isn't a digit and
# parse the result. Event names land in the second column after the
# stripped number; we match them by suffix to be tolerant of
# `cycles:u` / `cycles:k` variants emitted on some kernels.
_LINE_RE = re.compile(
    r"^\s*(?P<value>[\d.,'_  \s]+|<not supported>|<not counted>)"
    r"\s+(?P<event>[A-Za-z_:-]+)"
)


def _strip_separators(s: str) -> str:
    """Keep only digits and a single decimal point. Locale-agnostic."""
    out = []
    seen_dot = False
    for c in s:
        if c.isdigit():
            out.append(c)
        elif c == "." and not seen_dot:
            out.append(c)
            seen_dot = True
        # Everything else (commas, spaces, NBSP, narrow-NBSP, apostrophes…)
        # is a thousands separator we want to drop.
    return "".join(out)


def parse_perf_stat(text: str) -> Dict[str, int]:
    """Return {event_name: count} from `perf stat` stderr output.

    Events with `<not supported>` / `<not counted>` come back as 0 so
    callers can detect missing PMU support without separate logic.
    Event keys are stored as the bare suffix (e.g. "cycles" rather
    than "cycles:u"), which is what the Store expects.
    """
    out: Dict[str, int] = {}
    for raw in text.splitlines():
        m = _LINE_RE.match(raw)
        if not m:
            continue
        value_field = m.group("value").strip()
        event = m.group("event").split(":", 1)[0]
        if value_field.startswith("<"):
            out[event] = 0
            continue
        digits = _strip_separators(value_field)
        if not digits:
            continue
        try:
            out[event] = int(float(digits))
        except ValueError:
            continue
    return out


def derive_ratios(counters: Dict[str, int]
                  ) -> Tuple[float, float, float]:
    """(ipc, cache_miss_rate, branch_miss_rate). 0.0 when the
    underlying counter pair is missing or zero."""
    cycles = counters.get("cycles", 0)
    inst = counters.get("instructions", 0)
    crefs = counters.get("cache-references", 0)
    cmiss = counters.get("cache-misses", 0)
    bmiss = counters.get("branch-misses", 0)
    ipc = inst / cycles if cycles > 0 else 0.0
    cm_rate = cmiss / crefs if crefs > 0 else 0.0
    bm_rate = bmiss / inst if inst > 0 else 0.0
    return ipc, cm_rate, bm_rate


async def _run_perf_stat(perf_bin: str, pid: int,
                         window_seconds: int) -> Tuple[bool, int, str]:
    """Run `perf stat -e ... -p PID -- sleep N`. Counters come out on
    stderr; we capture both streams just in case some perf builds
    redirect differently.
    """
    cmd = [
        perf_bin, "stat",
        "-x", ",",
        "-e", "cycles,instructions,cache-references,cache-misses,branch-misses",
        "-p", str(pid),
        "--", "sleep", str(window_seconds),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1
    text = (stderr.decode("utf-8", errors="replace") + "\n"
            + stdout.decode("utf-8", errors="replace"))
    return rc == 0, rc, text


# `perf stat -x ,` (the "machine-readable" form) is more reliable to
# parse than the prose output — each line is `count,unit,event,...`
# with empty fields for counters that didn't run. Use it when
# available; the parse_perf_stat helper handles both flavours since
# the regex matches on the trailing event name either way.
_X_RE = re.compile(
    r"^(?P<value>[^,]*),[^,]*,(?P<event>[A-Za-z_:-]+)"
)


def parse_perf_stat_x(text: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for raw in text.splitlines():
        m = _X_RE.match(raw)
        if not m:
            continue
        value_field = m.group("value").strip()
        event = m.group("event").split(":", 1)[0]
        if not value_field or value_field.startswith("<"):
            out[event] = 0
            continue
        digits = _strip_separators(value_field)
        if not digits:
            continue
        try:
            out[event] = int(float(digits))
        except ValueError:
            continue
    return out


async def perfstat_loop(store, interval: float = 10.0,
                        window_seconds: float = 3.0) -> None:
    """Cycle: pick the focused PID, run perf stat for `window_seconds`,
    record one sample. Idle for `interval - window_seconds` between
    cycles. Mirrors perftop's pacing so the two share the same
    duty-cycle budget on the target.
    """
    ok, info = profile_caps.have_perf()
    if not ok:
        store.perfstat_status = info
        store.perfstat_enabled = False
        return
    perf_bin = shutil.which("perf")
    if not perf_bin:
        store.perfstat_status = "perf disappeared from PATH"
        store.perfstat_enabled = False
        return
    store.perfstat_enabled = True
    store.perfstat_status = f"waiting for PID ({perf_bin})"
    win = max(1, int(window_seconds))
    while True:
        pid: Optional[int] = store.proc_selected()
        if pid is None:
            await asyncio.sleep(0.5)
            continue
        store.perfstat_status = f"sampling pid={pid} ({win}s)"
        try:
            success, rc, output = await _run_perf_stat(perf_bin, pid, win)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.perfstat_status = f"run error: {e}"
            store.perfstat_last_error = str(e)
            await asyncio.sleep(interval)
            continue
        if not success:
            first = (output.splitlines() or [""])[0].strip()[:120]
            store.perfstat_status = f"failed (exit={rc}): {first}"
            store.perfstat_last_error = output
            await asyncio.sleep(interval)
            continue
        # Try the -x ,  parser first (preferred); fall back to the
        # prose parser if that returned nothing useful (some old perf
        # builds ignore -x and emit prose anyway).
        counters = parse_perf_stat_x(output)
        if not any(counters.values()):
            counters = parse_perf_stat(output)
        store.perfstat_last_error = ""
        ipc, cm_rate, bm_rate = derive_ratios(counters)
        store.perfstat_record_sample(time.time(), pid, ipc, cm_rate, bm_rate,
                                     counters)
        detectors.detect_perfstat_low_ipc(store)
        store.perfstat_status = (
            f"ok pid={pid} IPC={ipc:.2f} cache_miss={cm_rate*100:.1f}%"
        )
        target = time.time() + max(0.0, interval - win)
        while time.time() < target:
            if store.proc_selected() != pid:
                break
            await asyncio.sleep(0.5)
