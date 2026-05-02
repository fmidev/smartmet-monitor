"""Off-CPU stack sampler for the focused smartmetd PID.

Captures the time threads spend *blocked* (waiting on a lock, sleeping
in I/O, parked in a futex) together with the call stack that put them
to sleep. Renders into the same flamegraph layout as the on-CPU view,
just weighted by microseconds-blocked rather than sample-count.

Why this matters: a request that "feels slow" but shows nothing in the
on-CPU flamegraph is almost always blocked — on a database round-trip,
on file I/O, on a contended mutex. Off-CPU profiling is the canonical
answer to that class of question (see Brendan Gregg's "Off-CPU
Analysis" articles).

Backend selection (handled by profile_caps.offcpu_backend):

  1. `offcputime-bpfcc` — eBPF-based, low overhead. Preferred.
  2. `perf record -e sched:sched_switch` — fallback, higher overhead.
     Only attempted when bcc-tools is missing.

Both produce *folded* stacks: one line per unique (thread, stack)
combination, with the total off-CPU time at the end. We keep stacks
plus weights in a bounded ring; the flame view weights tree counts
by the stored microseconds.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import List, Optional, Tuple

from . import profile_caps


# A folded line looks like:
#   smartmetd;[unknown];Engine::run;...;futex_wait_queue_me 1234567
# Split off the trailing integer (microseconds) and treat the
# semicolon-joined prefix as the stack root → leaf.
_FOLDED_RE = re.compile(r"^(?P<stack>.+?)\s+(?P<us>\d+)\s*$")


def parse_offcputime_folded(text: str) -> List[Tuple[Tuple[str, ...], int]]:
    """Parse `offcputime-bpfcc -f` output into [(stack, microseconds)].

    bcc-tools emits root-first folded stacks (process-name first,
    leaf last) — exactly the order our flame tree builder wants —
    so no reversal is needed here. Lines that don't end in an
    integer are silently skipped (they appear in some bcc versions
    when a stack is truncated or an internal counter overflows).
    """
    out: List[Tuple[Tuple[str, ...], int]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _FOLDED_RE.match(line)
        if not m:
            continue
        try:
            us = int(m.group("us"))
        except ValueError:
            continue
        if us <= 0:
            continue
        frames = tuple(f for f in m.group("stack").split(";") if f)
        if not frames:
            continue
        out.append((frames, us))
    return out


async def _run_offcputime(binary: str, pid: int,
                          record_seconds: int) -> Tuple[bool, int, str]:
    """Run `offcputime-bpfcc -p PID -f SECONDS`. Returns (ok, rc, stdout|stderr).

    `-f` requests folded output (one stack per line + microseconds).
    `-p` filters to one process; without it we'd get every kernel
    thread on the host. Some bcc builds also accept `-U` (user-stack
    only) and `-K` (kernel-stack only); we leave both off so the
    operator gets the full call chain (user → kernel) the way Gregg's
    examples do.

    The tool needs root or CAP_SYS_ADMIN — same constraint as the
    existing perf path. Failure messages are surfaced verbatim into
    the panel so the operator can act on them.
    """
    cmd = [binary, "-p", str(pid), "-f", str(record_seconds)]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1
    if rc != 0:
        diag = (stderr.decode("utf-8", errors="replace") + "\n"
                + stdout.decode("utf-8", errors="replace")).strip()
        return False, rc, diag
    return True, 0, stdout.decode("utf-8", errors="replace")


async def offcpu_loop(store, interval: float = 10.0,
                      record_seconds: int = 3) -> None:
    """Periodically sample off-CPU stacks for the focused PID.

    Mirrors the shape of perftop.perf_loop so the operator's mental
    model carries over: pick a PID via the Proc / Flame panel, the
    loop targets that PID, and an empty `interval - record_seconds`
    idle window keeps duty cycle bounded.
    """
    backend, info = profile_caps.offcpu_backend()
    if backend == "":
        store.offcpu_status = info  # the install hint is the status
        store.offcpu_enabled = False
        return
    store.offcpu_backend = backend
    if backend == "bcc":
        bcc_path = profile_caps._find_bcc_tool("offcputime-bpfcc", "offcputime")
        if not bcc_path:
            # Lost between probe and use — extremely unlikely but possible
            # if /usr changed or bcc-tools was uninstalled mid-session.
            store.offcpu_status = "offcputime disappeared between probe and use"
            store.offcpu_enabled = False
            return
        store.offcpu_enabled = True
        store.offcpu_status = f"waiting for PID (backend=bcc: {bcc_path})"
        await _run_bcc_loop(store, bcc_path, interval, record_seconds)
        return
    # Pure-perf fallback. Not implemented yet — it requires sched_switch
    # + sched_stat_blocked correlation in Python, which is significantly
    # more complex than parsing offcputime's folded output. We surface
    # an honest message instead of pretending to be running.
    store.offcpu_status = (
        "off-CPU pure-perf path not implemented; "
        "install bcc-tools (dnf install bcc-tools) for off-CPU profiling"
    )
    store.offcpu_enabled = False


async def _run_bcc_loop(store, binary: str, interval: float,
                        record_seconds: int) -> None:
    while True:
        if getattr(store, "profile_paused", False):
            await asyncio.sleep(0.5)
            continue
        pid: Optional[int] = store.proc_selected()
        if pid is None:
            await asyncio.sleep(0.5)
            continue
        rs = max(1, int(record_seconds))
        store.offcpu_target_pid = pid
        store.offcpu_status = f"recording pid={pid} ({rs}s, bcc)"
        try:
            ok, rc, output = await _run_offcputime(binary, pid, rs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            store.offcpu_status = f"record error: {e}"
            store.offcpu_last_error = str(e)
            await asyncio.sleep(interval)
            continue
        if not ok:
            first = (output.splitlines() or [""])[0].strip()
            short = first[:120] if first else f"exit={rc}"
            store.offcpu_status = f"record failed (exit={rc}): {short}"
            store.offcpu_last_error = (
                output or f"offcputime exited with {rc} and no diagnostic output"
            )
            await asyncio.sleep(interval)
            continue
        store.offcpu_last_error = ""
        weighted = parse_offcputime_folded(output)
        store.offcpu_record_samples(pid, time.time(), weighted)
        total_us = sum(w for _, w in weighted)
        store.offcpu_status = (
            f"ok pid={pid} stacks={len(weighted)} total_off_ms={total_us//1000}"
        )
        # Idle remainder of duty cycle, with early exit on PID switch.
        target = time.time() + max(0.0, interval - rs)
        while time.time() < target:
            if store.proc_selected() != pid:
                break
            await asyncio.sleep(0.5)
