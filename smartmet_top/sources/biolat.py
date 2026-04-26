"""Block-I/O latency sampler.

Periodically runs `biolatency-bpfcc INTERVAL 1` and parses the
power-of-2 histogram into (p50, p95, p99) microsecond percentiles
plus a total ops count for the window. The Proc panel renders the
result as numbers + a p95 sparkline.

biolatency-bpfcc operates at the block layer (after the elevator),
so it captures real device-level latency for both direct block I/O
and major page faults that hit storage. It is host-wide rather
than per-PID — the bcc tool does not expose a process filter at
this level — but on a dedicated SmartMet host the dominant block
I/O is from smartmetd anyway, and host-level numbers answer the
"is the disk slow right now?" question directly.

Backend: bcc-tools only. There is no perf-only fallback for this;
`block:block_rq_complete` parsing in user space would work but the
overhead of recording every completion via perf is significantly
higher than bcc's eBPF aggregation. If bcc-tools isn't installed,
the loop sets a status string and exits — same pattern as the
off-CPU recorder.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from typing import List, Optional, Tuple

from . import detectors, profile_caps


# A bucket line looks like: "         8 -> 15         : 173      |***...|"
# The trailing distribution bar is matched optionally — biolatency
# always prints it in real output but stripping it lets the parser
# accept hand-edited examples and synthetic test fixtures.
_BUCKET_RE = re.compile(
    r"^\s*(?P<lo>\d+)\s*->\s*(?P<hi>\d+)\s*:\s*(?P<cnt>\d+)\s*(?:\||$)"
)
# The unit header line: "     usecs               : count     distribution"
# "usecs" by default; "msecs" if biolatency was invoked with -m. We don't
# pass -m (microsecond resolution is better for the fast/slow mix that
# typical SmartMet workloads produce) but the parser handles both for
# robustness.
_UNIT_RE = re.compile(r"^\s*(?P<unit>usecs|msecs|nsecs)\s*:")


def parse_biolatency(text: str) -> Tuple[List[Tuple[int, int, int]], str]:
    """Parse biolatency output into ([(lo, hi, count), ...], unit_string).

    Buckets are returned in input order (which is ascending latency).
    `unit_string` is one of "usecs" | "msecs" | "nsecs"; defaults to
    "usecs" if the header isn't found, which matches biolatency's own
    default. Multiple histograms in the same output (when biolatency
    is asked for several intervals) are concatenated — callers that
    want only the latest should pass a single-cycle invocation.
    """
    buckets: List[Tuple[int, int, int]] = []
    unit = "usecs"
    for raw in text.splitlines():
        m = _UNIT_RE.match(raw)
        if m:
            unit = m.group("unit")
            continue
        m = _BUCKET_RE.match(raw)
        if not m:
            continue
        try:
            buckets.append((int(m.group("lo")), int(m.group("hi")),
                            int(m.group("cnt"))))
        except ValueError:
            continue
    return buckets, unit


def _to_microseconds(value: int, unit: str) -> int:
    if unit == "usecs":
        return value
    if unit == "msecs":
        return value * 1000
    if unit == "nsecs":
        return value // 1000
    return value


def percentiles_us(buckets: List[Tuple[int, int, int]],
                   unit: str) -> Tuple[int, int, int, int]:
    """Return (p50, p95, p99, total) in microseconds.

    Each bucket is `lo -> hi : count` covering the half-open range
    [lo, hi]. We use the bucket's `hi` as the conservative percentile
    estimate (i.e. "this percentile of operations completed in at
    most `hi` units"), which matches how biolatency itself reports
    summaries.
    """
    total = sum(c for _, _, c in buckets)
    if total <= 0:
        return 0, 0, 0, 0
    targets = (
        ("p50", total * 50 // 100),
        ("p95", total * 95 // 100),
        ("p99", total * 99 // 100),
    )
    found = {"p50": 0, "p95": 0, "p99": 0}
    cum = 0
    for lo, hi, cnt in buckets:
        cum += cnt
        for name, threshold in targets:
            if found[name] == 0 and cum >= threshold:
                found[name] = _to_microseconds(hi, unit)
    return found["p50"], found["p95"], found["p99"], total


async def _run_biolatency(binary: str, window_seconds: int
                          ) -> Tuple[bool, int, str]:
    """Run `biolatency-bpfcc INTERVAL 1`. Blocks for ~INTERVAL seconds
    while bcc collects samples, then prints one histogram on stdout.
    Returns (ok, returncode, combined_output)."""
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


async def biolat_loop(store, window_seconds: float = 5.0) -> None:
    """Cycle: run biolatency for `window_seconds`, record one sample.

    The window doubles as the loop's idle period — we don't add an
    additional sleep because biolatency itself blocks for the
    measurement window. Default 5 s gives one sample every 5 s,
    which is dense enough for a sparkline and light enough on the
    target.
    """
    ok, info = profile_caps.have_biolatency_bcc()
    if not ok:
        store.biolat_status = info
        store.biolat_enabled = False
        return
    binary: Optional[str] = (shutil.which("biolatency-bpfcc")
                              or shutil.which("biolatency"))
    if not binary:
        store.biolat_status = "biolatency disappeared from PATH"
        store.biolat_enabled = False
        return
    store.biolat_enabled = True
    store.biolat_status = f"running ({binary})"
    win_int = max(1, int(window_seconds))
    while True:
        try:
            success, rc, output = await _run_biolatency(binary, win_int)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.biolat_status = f"run error: {e}"
            store.biolat_last_error = str(e)
            await asyncio.sleep(win_int)
            continue
        if not success:
            first = (output.splitlines() or [""])[0].strip()[:120]
            store.biolat_status = f"failed (exit={rc}): {first}"
            store.biolat_last_error = output
            await asyncio.sleep(win_int)
            continue
        store.biolat_last_error = ""
        buckets, unit = parse_biolatency(output)
        p50, p95, p99, total = percentiles_us(buckets, unit)
        store.biolat_record_sample(time.time(), p50, p95, p99, total)
        detectors.detect_biolat_slow(store)
        if total > 0:
            iops = total / win_int
            store.biolat_status = (
                f"ok iops={iops:.0f} p95={p95}us p99={p99}us"
            )
        else:
            store.biolat_status = "ok (no block I/O in window)"
