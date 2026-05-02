"""User-space allocation flamegraph — DEVELOPMENT-ONLY profiler.

⚠ WARNING ⚠
This recorder uses bpftrace uprobes on the allocator's `malloc()`
entry point. Every malloc call in the target process triggers a
kernel breakpoint that bpftrace has to handle. On a busy SmartMet
backend this can mean *millions* of breakpoints per second and
measurable CPU overhead — sometimes enough to slow request
handling visibly. **DO NOT enable this on production.** Run it on
a dev / staging server, ideally one explicitly set up for
profiling.

Mitigation. The recorder filters to allocations of at least
`min_bytes` (default 4096) so only the operationally interesting
cases remain. Most production overhead comes from millions of
small allocations (string concat, small struct copies); a 4 KB
threshold removes those from the trace and keeps the bigger
allocations — vector resizes, buffer pools, deserialisation
output buffers — which are the ones an operator actually wants to
see. `--malloc-flame 0` disables the filter (extreme overhead).

Allocator detection. SmartMet usually runs with jemalloc
(`libjemalloc.so.2`) and may switch to mimalloc
(`libmimalloc.so`). Stock glibc malloc is also supported as a
fallback. The recorder scans `/proc/PID/maps` to pick the right
library to uprobe; the symbol name is the same (`malloc`) in
every case because both jemalloc and mimalloc export `malloc` as
their public entry point.

Backend: bpftrace. Requires the `bpftrace` package (RHEL 8 ships
it in `bpftrace`; Fedora the same; Debian / Ubuntu in `bpftrace`).
Uprobes have been stable since kernel 4.18 so RHEL 8 is fully
supported.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from typing import List, Optional, Tuple


def detect_allocator(pid: int) -> Optional[Tuple[str, str]]:
    """Return (allocator_name, library_path) by scanning /proc/PID/maps.

    Order of preference: jemalloc → mimalloc → glibc. The first
    library matched wins. Returns None if /proc/PID/maps is not
    readable (process exited, or smtop running without permission).
    """
    try:
        with open(f"/proc/{pid}/maps", "r", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    for needle, name in (
        ("libjemalloc", "jemalloc"),
        ("libmimalloc", "mimalloc"),
    ):
        for line in text.splitlines():
            # `r-x` segment is the executable mapping — that's the
            # one bpftrace needs to attach the uprobe to. Skipping
            # `r--` (read-only data) and `rw-` (writable data)
            # avoids attaching to the wrong mapping.
            if needle in line and " r-x" in line:
                return name, line.split()[-1]
    # Fallback: stock glibc malloc lives in libc.so.6 (or libc.musl-…
    # on Alpine, but we don't ship there). This gives the operator
    # SOME profiling on hosts running unmodified glibc.
    for line in text.splitlines():
        if " r-x" in line and (
            "/libc.so." in line or "/libc-" in line
        ):
            return "glibc", line.split()[-1]
    return None


def parse_bpftrace_stacks(text: str) -> List[Tuple[Tuple[str, ...], int]]:
    """Parse `@[stack]: count` blocks from bpftrace output.

    bpftrace's standard output format for stack-keyed maps is

        @[
                fn1+0xN
                fn2+0xN
                ...
        ]: COUNT

    where the leaf is at the top (frames listed leaf → root). We
    reverse so the returned tuples match the rest of smtop's flame
    convention (root → leaf). Symbol offsets are stripped.
    """
    out: List[Tuple[Tuple[str, ...], int]] = []
    in_stack = False
    frames: List[str] = []
    for raw in text.splitlines():
        s = raw.rstrip()
        if not s:
            continue
        if s == "@[":
            in_stack = True
            frames = []
            continue
        if in_stack and s.startswith("]:"):
            try:
                count = int(s[2:].strip())
            except ValueError:
                count = 0
            if frames and count > 0:
                out.append((tuple(reversed(frames)), count))
            in_stack = False
            frames = []
            continue
        if in_stack:
            frame = s.strip()
            plus = frame.rfind("+")
            if plus > 0:
                frame = frame[:plus]
            if frame and frame != "???":
                frames.append(frame)
    return out


async def _run_bpftrace(bpftrace_bin: str, lib_path: str, pid: int,
                       min_bytes: int, record_seconds: int
                       ) -> Tuple[bool, int, str]:
    """Run a one-shot bpftrace script that records malloc stacks
    larger than `min_bytes` for `record_seconds` and exits.

    The script aggregates by stack and weights by total bytes
    (`sum(arg0)`), which gives "bytes allocated per code path" —
    the most operationally meaningful weighting since two
    256-byte allocations matter less than one 1 MB allocation.
    """
    script = (
        f"uprobe:{lib_path}:malloc "
        f"/pid == $1 && arg0 >= $2/ "
        f"{{ @[ustack] = sum(arg0); }} "
        f"interval:s:$3 {{ exit(); }}"
    )
    proc = await asyncio.create_subprocess_exec(
        bpftrace_bin, "-e", script,
        str(pid), str(min_bytes), str(record_seconds),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1
    out_text = stdout.decode("utf-8", errors="replace")
    err_text = stderr.decode("utf-8", errors="replace")
    if rc != 0:
        return False, rc, (err_text + "\n" + out_text).strip()
    return True, 0, out_text


async def mallocflame_loop(store, min_bytes: int = 4096,
                           interval: float = 30.0,
                           record_seconds: int = 5) -> None:
    """Cycle: detect allocator, record allocation stacks via bpftrace
    for `record_seconds`, push to Store. Long idle window between
    cycles (default 30 s) compared to the perf-based recorders
    because the per-cycle uprobe overhead is high enough that we
    don't want to run continuously.
    """
    bpftrace_bin = shutil.which("bpftrace")
    if not bpftrace_bin:
        store.malloc_status = (
            "bpftrace not in PATH "
            "(RHEL/Fedora: dnf install bpftrace; Debian: apt install bpftrace)"
        )
        store.malloc_enabled = False
        return
    store.malloc_enabled = True
    store.malloc_status = (
        f"waiting for PID (bpftrace, min_bytes={min_bytes})"
    )
    rs = max(1, int(record_seconds))
    while True:
        if getattr(store, "profile_paused", False):
            await asyncio.sleep(0.5)
            continue
        pid: Optional[int] = store.proc_selected()
        if pid is None:
            await asyncio.sleep(0.5)
            continue
        alloc = detect_allocator(pid)
        if alloc is None:
            store.malloc_status = (
                f"no allocator library detected in pid {pid}"
            )
            await asyncio.sleep(interval)
            continue
        name, lib_path = alloc
        store.malloc_allocator = f"{name} ({lib_path})"
        store.malloc_status = (
            f"recording pid={pid} {name} ({rs}s, ≥{min_bytes} bytes)"
        )
        try:
            ok, rc, output = await _run_bpftrace(
                bpftrace_bin, lib_path, pid, min_bytes, rs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.malloc_status = f"run error: {e}"
            store.malloc_last_error = str(e)
            await asyncio.sleep(interval)
            continue
        if not ok:
            first = (output.splitlines() or [""])[0].strip()[:120]
            store.malloc_status = f"failed (exit={rc}): {first}"
            store.malloc_last_error = output
            await asyncio.sleep(interval)
            continue
        store.malloc_last_error = ""
        weighted = parse_bpftrace_stacks(output)
        store.malloc_record_samples(pid, time.time(), weighted)
        total_bytes = sum(w for _, w in weighted)
        store.malloc_status = (
            f"ok pid={pid} stacks={len(weighted)} "
            f"total_bytes={total_bytes}"
        )
        target = time.time() + max(0.0, interval - rs)
        while time.time() < target:
            if store.proc_selected() != pid:
                break
            await asyncio.sleep(0.5)
