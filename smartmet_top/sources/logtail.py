"""Async `tail -F` over one or more access-log files.

Uses plain file seek/read with a small sleep — avoids forking external
`tail` so we stay pure stdlib. When a file rotates (inode changes) we
reopen.

`bulk_load` (used by `--replay`) optionally walks rotated siblings of
each path so the operator can replay multiple days of history. SmartMet's
logrotate config produces files like:

    wms-access-log                  (current, uncompressed)
    wms-access-log-YYYYMMDD         (yesterday, not yet compressed)
    wms-access-log-YYYYMMDD.gz      (older, compressed)

`expand_rotated_paths()` returns all of them in chronological order so
the store sees historical lines first, then the current tail.
"""

from __future__ import annotations

import asyncio
import gzip
import os
import re
from typing import Iterable, List, Optional

from .logparse import parse


# Match `<base>-YYYYMMDD` and `<base>-YYYYMMDD.gz` for any rotation.
_ROTATED_RE_TMPL = r"^{base}-(\d{{8}})(\.gz)?$"


def source_label_for(path: str) -> str:
    """Derive a short plugin label from an access-log path.

    `/var/log/smartmet/wms-access-log` → `wms`. Anything that doesn't end
    in `-access-log` keeps its full basename (the operator may have a
    custom layout — we'd rather show "weird-thing.log" than misclassify
    it as "weird-thing.log").
    """
    base = os.path.basename(path)
    suffix = "-access-log"
    if base.endswith(suffix):
        return base[: -len(suffix)] or base
    # Rotated variants share the source label of the live log: drop the
    # trailing `-YYYYMMDD(.gz)?` so all of yesterday's `wms-access-log-*`
    # files map to the same `wms` source as the current file.
    m = re.match(rf"^(?P<base>.+?)-access-log-\d{{8}}(\.gz)?$", base)
    if m:
        return m.group("base") or base
    return base


def expand_rotated_paths(path: str) -> List[str]:
    """Return rotated siblings of `path` in chronological order, with
    `path` itself as the final entry.

    Looks for `<basename>-YYYYMMDD` and `<basename>-YYYYMMDD.gz` files
    in the same directory, sorts them by the date suffix (oldest first)
    and appends the live log last so the store ingests historical
    lines before live ones — important for the per-minute pruning
    logic, which uses the line's timestamp (not wall-clock) as the
    cutoff origin.

    Returns just `[path]` if no rotated siblings exist or the directory
    can't be read.
    """
    base = os.path.basename(path)
    dir_ = os.path.dirname(path) or "."
    try:
        entries = os.listdir(dir_)
    except OSError:
        return [path]
    pattern = re.compile(_ROTATED_RE_TMPL.format(base=re.escape(base)))
    # Group by date — logrotate may transiently leave both `<base>-DATE`
    # and `<base>-DATE.gz` while compression is mid-run. Prefer the
    # compressed (finalized) version so we don't double-count.
    by_date: dict = {}
    for entry in entries:
        m = pattern.match(entry)
        if not m:
            continue
        date, gz = m.group(1), m.group(2) or ""
        existing = by_date.get(date)
        # Prefer .gz (gz != "") over plain when both are present.
        if existing is None or (gz and not existing[1]):
            by_date[date] = (os.path.join(dir_, entry), gz)
    out = [p for _, (p, _) in sorted(by_date.items())]
    out.append(path)
    return out


def _open_log(path: str):
    """Open `path` for reading, transparently handling gzip."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


class _TailedFile:
    def __init__(self, path: str) -> None:
        self.path = path
        self.label = source_label_for(path)
        self.fh = None
        self.inode: Optional[int] = None
        self._open()

    def _open(self) -> None:
        try:
            self.fh = open(self.path, "r", encoding="utf-8", errors="replace")
            st = os.stat(self.path)
            self.inode = st.st_ino
            # seek to end so we only read new data
            self.fh.seek(0, os.SEEK_END)
        except FileNotFoundError:
            self.fh = None
            self.inode = None

    def _maybe_rotate(self) -> None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            if self.fh:
                self.fh.close()
            self.fh = None
            self.inode = None
            return
        if self.inode is None:
            self._open()
            return
        if st.st_ino != self.inode:
            # rotated
            if self.fh:
                self.fh.close()
            self._open()
            # after rotation read from the beginning
            if self.fh:
                self.fh.seek(0)
                self.inode = st.st_ino

    def read_new(self) -> List[str]:
        self._maybe_rotate()
        if self.fh is None:
            return []
        out: List[str] = []
        while True:
            line = self.fh.readline()
            if not line:
                break
            out.append(line.rstrip("\n"))
        return out


async def tail_many(paths: Iterable[str], store, poll_interval: float = 0.25) -> None:
    """Tail all paths forever, feeding parsed records into `store`."""
    files = [_TailedFile(p) for p in paths]
    # Pre-register sources so the Plugins panel shows idle handlers as
    # rows rather than hiding them until the first request lands.
    for f in files:
        store.register_source(f.label)
    store.logtail_status = f"tailing {len(files)} file(s)"

    while True:
        any_data = False
        for f in files:
            try:
                lines = f.read_new()
            except Exception as e:
                store.logtail_status = f"error: {e}"
                continue
            for line in lines:
                any_data = True
                store.record_raw_line(line, source=f.label)
                rec = parse(line)
                if rec is None:
                    continue
                store.record_request(
                    ts=rec["start_ts"],
                    url=rec["path"],
                    dur_ms=rec["dur_ms"],
                    nbytes=rec["bytes"],
                    status=rec["status"],
                    apikey=rec["apikey"],
                    source_label=f.label,
                    ip=rec["ip"],
                )
        if any_data:
            store.logtail_status = f"tailing {len(files)} file(s)"
        await asyncio.sleep(poll_interval)


def _bulk_load_one_file(path: str, store, max_bytes_per_file: int) -> None:
    """Synchronous body of bulk_load — runs in a thread executor so
    the asyncio event loop stays free to schedule sampler tasks in
    parallel with replay. Was previously inlined in `bulk_load` but
    that made bulk_load `async def` without any await points, which
    silently monopolised the event loop for the full replay duration
    — perf_loop / proc_loop / netstats_loop / etc. were scheduled
    but couldn't run until replay finished, leaving the dashboard
    showing initial-state ('disabled' / 'not started') strings for
    every sampler during the replay window. Off-loading to an
    executor lets the loop tick normally.
    """
    label = source_label_for(path)
    store.register_source(label)
    try:
        size = os.path.getsize(path)
        with _open_log(path) as fh:
            # Seek-tail only works on the uncompressed live log;
            # gzip files don't support cheap arbitrary-position
            # seeks, so we read them fully and let the store's
            # minute-bucket pruning bound memory.
            is_gz = path.endswith(".gz")
            if not is_gz and size > max_bytes_per_file:
                fh.seek(size - max_bytes_per_file)
                fh.readline()  # skip partial line
            # Stop at the size we observed when we opened the file.
            # Without this guard, a live access log that smartmetd
            # is currently writing to keeps growing past our seek
            # position, ``for line in fh`` never reaches EOF, and
            # the replay banner stays stuck on that file forever
            # while we tail behind the daemon. ``.gz`` files don't
            # have a meaningful uncompressed size from stat, so we
            # let them read to natural EOF (a static rotated
            # archive isn't growing under us).
            if is_gz:
                lines_iter = fh
            else:
                end = size
                def _bounded():
                    while fh.tell() < end:
                        line = fh.readline()
                        if not line:
                            break
                        yield line
                lines_iter = _bounded()
            for line in lines_iter:
                line = line.rstrip("\n")
                store.record_raw_line(line, source=label)
                rec = parse(line)
                if rec is None:
                    continue
                store.record_request(
                    ts=rec["start_ts"],
                    url=rec["path"],
                    dur_ms=rec["dur_ms"],
                    nbytes=rec["bytes"],
                    status=rec["status"],
                    apikey=rec["apikey"],
                    source_label=label,
                    ip=rec["ip"],
                )
    except FileNotFoundError:
        return
    except OSError as e:
        # Surface I/O / gzip-corruption errors to the operator
        # rather than crashing the whole replay.
        store.logtail_status = f"replay error on {path}: {e}"


async def bulk_load(paths: Iterable[str], store,
                    max_bytes_per_file: int = 256 * 1024 * 1024,
                    include_rotated: bool = False) -> None:
    """One-shot load: read each file (bounded by `max_bytes_per_file`)
    and feed into the store. Used by `--replay`.

    With `include_rotated=True`, each input path is expanded to its
    rotated siblings (oldest first, current last) so the store sees a
    week or more of history when the host's logrotate keeps that long.
    `.gz` files are read transparently via `gzip.open`.

    The byte cap applies to each individual file (rotated *or* live),
    not to the union, and is taken from the tail — so on a 1.2 GB
    rotated daily log a 1 GB cap reads the most recent ~1 GB of
    that day's traffic, not the entire day. Raise `--replay-bytes`
    when a full day matters.

    Each file is read in a thread-pool executor so the asyncio event
    loop stays responsive — concurrent sampler tasks (perf_loop,
    proc_loop, etc.) scheduled before replay get CPU during replay,
    and the HTTP server keeps answering /api/* without lag.
    """
    loop = asyncio.get_event_loop()
    # Expand the rotation set up front so the dashboard's replay
    # banner can show real per-file progress (`5 / 22`) instead of
    # the user-supplied path count, which on `--include-rotated`
    # underestimates by an order of magnitude.
    actual_files: List[str] = []
    for p in paths:
        if include_rotated:
            actual_files.extend(expand_rotated_paths(p))
        else:
            actual_files.append(p)
    rs = getattr(store, "replay_status", None)
    if isinstance(rs, dict) and rs.get("in_progress"):
        rs["files_total"] = len(actual_files)
        rs["files_done"] = 0
    for actual in actual_files:
        if isinstance(rs, dict) and rs.get("in_progress"):
            rs["current_file"] = actual
        # Pre-flight: skip the per-file executor when the file is
        # missing, isn't a regular file, or is a non-gzip file of
        # zero bytes. These cases produce no records and can in
        # exotic setups (e.g. a logger writing to a FIFO that we
        # mistake for a regular log) make the executor block on
        # ``open()`` or ``read()``. Filtering up front keeps the
        # replay banner moving and guarantees the loop terminates.
        skip = False
        try:
            st = os.stat(actual)
            import stat as _stat
            if not _stat.S_ISREG(st.st_mode):
                skip = True
            elif not actual.endswith(".gz") and st.st_size == 0:
                skip = True
        except FileNotFoundError:
            skip = True
        except OSError:
            # Permission or filesystem error — _bulk_load_one_file
            # surfaces the message via logtail_status, but don't
            # stall the queue waiting on it.
            skip = True
        if not skip:
            await loop.run_in_executor(
                None, _bulk_load_one_file, actual, store,
                max_bytes_per_file)
        if isinstance(rs, dict) and rs.get("in_progress"):
            rs["files_done"] = int(rs.get("files_done", 0)) + 1
