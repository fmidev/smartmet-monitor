"""Per-process stats reader for SmartMet Server processes.

All data comes from /proc — pure stdlib, no `perf` or external tools.
We deliberately read only the cheap, kernel-maintained counters that
scale O(1) with the number of memory mappings (`status`, `statm`, `io`,
`stat`, the `fd` directory listing), because smartmetd routinely keeps
over a million file mappings open and per-VMA reads (`maps`, `smaps`)
take seconds to complete and hold mmap_sem on the target process.

The expensive `smaps_rollup` rollup is exposed as an explicit on-demand
fetch via `read_smaps_rollup(pid)`. The polling loop never invokes it.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Dict, List, Optional


SMARTMETD_COMM = "smartmetd"
PROC_POLL_INTERVAL = 2.0     # status/statm/io are O(1) — safe at 2 s
FD_REFRESH_INTERVAL = 10.0   # listdir of /proc/PID/fd is cheap but not free
DISCOVERY_INTERVAL = 5.0     # rescan /proc for new/exited smartmetd PIDs


_KB_PATTERN = re.compile(r"^(?P<key>[A-Za-z_]+):\s+(?P<val>\d+)\s*kB", re.MULTILINE)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def discover_smartmetd_pids() -> List[int]:
    """Return PIDs whose /proc/PID/comm equals 'smartmetd'."""
    out: List[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/comm", "r", encoding="utf-8") as f:
                if f.read().strip() == SMARTMETD_COMM:
                    out.append(int(name))
        except OSError:
            continue
    return sorted(out)


def read_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            data = f.read()
    except OSError:
        return ""
    return data.replace(b"\x00", b" ").decode("utf-8", errors="replace").rstrip()


def detect_role(cmdline: str) -> str:
    """Best-effort role label. SmartMet operators usually name configs
    `smartmetd-frontend.conf` / `smartmetd-backend.conf`; if neither
    marker is present we say `unknown` and let the operator pick by hand
    via the PID selector."""
    cl = cmdline.lower()
    if "frontend" in cl or re.search(r"\bfe\b", cl):
        return "frontend"
    if "backend" in cl or re.search(r"\bbe\b", cl):
        return "backend"
    return "unknown"


def read_status(pid: int) -> Dict[str, int]:
    """Parse the kB fields and Threads from /proc/PID/status.

    Returns a dict keyed by exact field name (`VmRSS`, `RssAnon`, ...).
    Missing keys default to 0 in the caller; this function returns only
    what was present.
    """
    out: Dict[str, int] = {}
    try:
        text = _read_text(f"/proc/{pid}/status")
    except OSError:
        return out
    for m in _KB_PATTERN.finditer(text):
        out[m.group("key")] = int(m.group("val"))
    m = re.search(r"^Threads:\s+(\d+)", text, re.MULTILINE)
    if m:
        out["Threads"] = int(m.group(1))
    return out


def read_io(pid: int) -> Dict[str, int]:
    out: Dict[str, int] = {}
    try:
        text = _read_text(f"/proc/{pid}/io")
    except OSError:
        return out
    for line in text.splitlines():
        k, _, v = line.partition(":")
        try:
            out[k.strip()] = int(v.strip())
        except ValueError:
            continue
    return out


def count_fds(pid: int) -> int:
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        return 0


def _read_starttime_ticks(pid: int) -> Optional[int]:
    try:
        text = _read_text(f"/proc/{pid}/stat")
    except OSError:
        return None
    rp = text.rfind(")")
    if rp < 0:
        return None
    fields = text[rp + 2:].split()
    # Field 22 ("starttime") in the documented numbering — the comm field
    # is gone after slicing past ")", so it lands at index 19 here.
    if len(fields) <= 19:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


_BOOT_TIME_CACHE: Optional[float] = None


def _boot_time() -> float:
    global _BOOT_TIME_CACHE
    if _BOOT_TIME_CACHE is not None:
        return _BOOT_TIME_CACHE
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if line.startswith("btime "):
                    _BOOT_TIME_CACHE = float(line.split()[1])
                    return _BOOT_TIME_CACHE
    except OSError:
        pass
    _BOOT_TIME_CACHE = 0.0
    return 0.0


def process_started_at(pid: int) -> float:
    """Wall-clock epoch seconds at which the process started, or 0.0."""
    starttime = _read_starttime_ticks(pid)
    if starttime is None:
        return 0.0
    try:
        clk = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        clk = 100
    bt = _boot_time()
    if bt <= 0:
        return 0.0
    return bt + starttime / clk


def read_smaps_rollup(pid: int) -> Dict[str, int]:
    """Expensive on processes with millions of mappings — caller decides
    when. Returns kB values keyed by field name (Pss, Shared_Clean,
    Private_Dirty, Swap, ...); empty dict on permission failure or absence.
    """
    out: Dict[str, int] = {}
    try:
        text = _read_text(f"/proc/{pid}/smaps_rollup")
    except OSError:
        return out
    for m in _KB_PATTERN.finditer(text):
        out[m.group("key")] = int(m.group("val"))
    return out


# ---- async polling loop ----------------------------------------------------

async def proc_loop(store) -> None:
    """Discover smartmetd PIDs and poll memory/IO into the store forever."""
    last_discovery = 0.0
    last_fd = 0.0
    pids: Dict[int, dict] = {}  # pid -> {"fds": int}
    while True:
        now = time.time()

        if now - last_discovery >= DISCOVERY_INTERVAL:
            current = set(discover_smartmetd_pids())
            for pid in list(pids.keys()):
                if pid not in current:
                    store.proc_remove(pid)
                    pids.pop(pid, None)
            for pid in current - pids.keys():
                cmdline = read_cmdline(pid)
                role = detect_role(cmdline)
                started = process_started_at(pid)
                pids[pid] = {"fds": 0}
                store.proc_register(pid, cmdline=cmdline, role=role,
                                    started_at=started)
            last_discovery = now

        do_fd = (now - last_fd >= FD_REFRESH_INTERVAL)
        for pid in list(pids.keys()):
            status = read_status(pid)
            io = read_io(pid)
            if not status:
                # Process likely gone — drop on next discovery sweep.
                continue
            if do_fd:
                pids[pid]["fds"] = count_fds(pid)
            store.proc_update(
                pid=pid,
                ts=now,
                vm_rss_kb=status.get("VmRSS", 0),
                vm_size_kb=status.get("VmSize", 0),
                vm_swap_kb=status.get("VmSwap", 0),
                vm_pte_kb=status.get("VmPTE", 0),
                vm_hwm_kb=status.get("VmHWM", 0),
                rss_anon_kb=status.get("RssAnon", 0),
                rss_file_kb=status.get("RssFile", 0),
                rss_shmem_kb=status.get("RssShmem", 0),
                threads=status.get("Threads", 0),
                io_read_bytes=io.get("read_bytes", 0),
                io_write_bytes=io.get("write_bytes", 0),
                fds=pids[pid].get("fds", 0),
            )
        if do_fd:
            last_fd = now

        await asyncio.sleep(PROC_POLL_INTERVAL)
