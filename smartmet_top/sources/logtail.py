"""Async `tail -F` over one or more access-log files.

Uses plain file seek/read with a small sleep — avoids forking external
`tail` so we stay pure stdlib. When a file rotates (inode changes) we
reopen.
"""

from __future__ import annotations

import asyncio
import os
from typing import Iterable, List, Optional

from .logparse import parse


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
    return base


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
                store.record_raw_line(line)
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
                )
        if any_data:
            store.logtail_status = f"tailing {len(files)} file(s)"
        await asyncio.sleep(poll_interval)


async def bulk_load(paths: Iterable[str], store, max_bytes_per_file: int = 256 * 1024 * 1024) -> None:
    """One-shot load: read each file fully (bounded), feed into store. Useful for
    the `--replay` mode or initial backfill."""
    for p in paths:
        label = source_label_for(p)
        store.register_source(label)
        try:
            size = os.path.getsize(p)
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                if size > max_bytes_per_file:
                    fh.seek(size - max_bytes_per_file)
                    fh.readline()  # skip partial line
                for line in fh:
                    line = line.rstrip("\n")
                    store.record_raw_line(line)
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
                    )
        except FileNotFoundError:
            continue
