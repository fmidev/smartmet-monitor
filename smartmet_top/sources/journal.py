"""systemd-journal tail for the SmartMet service unit.

Spawns `journalctl -u <UNIT> -n N -f --output=short --no-pager` and
streams each line into the Store as a Logs source named
"journal". The Logs panel's per-source tab bar then exposes it
alongside the access-log sources, so the operator can flip between
"what is the application logging" (access logs) and "what is
systemd / the kernel saying about the application" (journal) in
one keystroke.

Why this matters. Access logs only show what smartmetd intends to
log — they are silent on kernel-side disasters: OOM-killer
intervention, cgroup throttling messages, segfault traces,
restart loops, the "stopped responding to ping, killed it"
sequence. journalctl shows all of that. When the access log goes
quiet during an incident, the journal usually has the answer.

Backend: just `journalctl` from systemd. Available on every
systemd-using distro (every supported one). On hosts without
systemd or without the unit configured, the loop sets a status
string and exits without taking any cycle time.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Optional


JOURNAL_SOURCE_LABEL = "journal"


async def journal_loop(store, unit: str, prefill_lines: int = 50) -> None:
    """Tail `journalctl -u UNIT -f` indefinitely, pushing every line
    into store.record_raw_line(line, source="journal"). Restarts the
    subprocess if it dies (the unit could be transiently absent;
    journalctl exits when that happens).
    """
    journalctl = shutil.which("journalctl")
    if not journalctl:
        store.journal_status = "journalctl not in PATH (no systemd?)"
        store.journal_enabled = False
        return
    if not unit:
        store.journal_status = "journal disabled (--journal-unit empty)"
        store.journal_enabled = False
        return
    store.register_source(JOURNAL_SOURCE_LABEL)
    store.journal_enabled = True
    store.journal_status = f"tailing -u {unit}"
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                journalctl,
                "-u", unit,
                "-n", str(prefill_lines),
                "-f",
                "--output=short",
                "--no-pager",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            store.journal_status = f"spawn failed: {e}"
            await asyncio.sleep(5)
            continue
        store.journal_status = f"tailing -u {unit} (pid {proc.pid})"
        # Read stdout line-by-line. The decoder tolerates malformed
        # bytes — journalctl is mostly UTF-8 but a panicked kernel
        # message may slip in something exotic, and we'd rather show
        # a replacement char than crash the loop.
        try:
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if line:
                    store.record_raw_line(line, source=JOURNAL_SOURCE_LABEL)
        except asyncio.CancelledError:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            raise
        except Exception as e:
            store.journal_status = f"read error: {e}"
        # Subprocess exited (unit absent, journalctl restarted, …).
        # Pause briefly and respawn — journal is a long-lived feed
        # so we want auto-recovery rather than terminating the loop.
        rc = proc.returncode if proc.returncode is not None else "?"
        store.journal_status = f"journalctl exited (rc={rc}) — restarting in 5s"
        await asyncio.sleep(5)
