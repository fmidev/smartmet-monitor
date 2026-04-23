"""Shared in-memory state for smartmet-top.

All data sources write here; all panels read from here. Updates are
timestamped so panels can render rolling windows without re-parsing.

Time bucketing: we keep a ring of per-minute stats for each URL. At
60 minutes of history a URL costs ~40*60 ints = ~20 KB, so 1000 URLs
= ~20 MB. Plenty.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import RLock
from typing import Deque, Dict, List, Optional, Tuple

from .histogram import Histogram

HISTORY_MINUTES = 60


@dataclass
class MinuteBucket:
    hist: Histogram = field(default_factory=Histogram)
    count: int = 0
    bytes: int = 0
    errors: int = 0
    status_counts: Dict[int, int] = field(default_factory=dict)


@dataclass
class UrlStats:
    """Rolling stats for one URL path."""

    url: str
    # minute_epoch -> MinuteBucket
    buckets: Dict[int, MinuteBucket] = field(default_factory=dict)
    apikey_counts: Dict[str, int] = field(default_factory=dict)
    first_seen: float = field(default_factory=time.time)
    last_seen: float = 0.0

    def record(self, ts: float, dur_ms: float, nbytes: int, status: int, apikey: str) -> None:
        m = int(ts // 60)
        b = self.buckets.get(m)
        if b is None:
            b = MinuteBucket()
            self.buckets[m] = b
            # prune old minutes
            cutoff = m - HISTORY_MINUTES
            for k in list(self.buckets.keys()):
                if k < cutoff:
                    del self.buckets[k]
        b.hist.add(dur_ms)
        b.count += 1
        b.bytes += nbytes
        if status >= 400:
            b.errors += 1
        b.status_counts[status] = b.status_counts.get(status, 0) + 1
        if apikey and apikey != "-":
            self.apikey_counts[apikey] = self.apikey_counts.get(apikey, 0) + 1
        self.last_seen = ts

    def window(self, minutes: int) -> MinuteBucket:
        """Merge last N minutes into one bucket."""
        now_min = int(time.time() // 60)
        merged = MinuteBucket()
        for m in range(now_min - minutes + 1, now_min + 1):
            b = self.buckets.get(m)
            if b is None:
                continue
            merged.hist.merge(b.hist)
            merged.count += b.count
            merged.bytes += b.bytes
            merged.errors += b.errors
            for s, c in b.status_counts.items():
                merged.status_counts[s] = merged.status_counts.get(s, 0) + c
        return merged

    def minute_series(self, minutes: int, metric: str) -> List[float]:
        """Return per-minute values for the last N minutes (oldest first)."""
        now_min = int(time.time() // 60)
        out: List[float] = []
        for m in range(now_min - minutes + 1, now_min + 1):
            b = self.buckets.get(m)
            if b is None or b.count == 0:
                out.append(0.0)
                continue
            if metric == "count":
                out.append(float(b.count))
            elif metric == "mean_ms":
                out.append(b.hist.mean())
            elif metric == "p95_ms":
                out.append(b.hist.p95())
            elif metric == "bytes":
                out.append(float(b.bytes))
            elif metric == "err_pct":
                out.append(b.errors / b.count * 100.0 if b.count else 0.0)
            else:
                out.append(0.0)
        return out


@dataclass
class AdminSnapshot:
    """Latest response from a single admin endpoint."""

    fetched_at: float = 0.0
    ok: bool = False
    error: str = ""
    rows: List[dict] = field(default_factory=list)


class Store:
    """Thread-safe store for URL stats and admin-plugin snapshots."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.urls: Dict[str, UrlStats] = {}
        self.total_requests: int = 0
        self.total_bytes: int = 0
        self.total_errors: int = 0
        # per-minute global metrics (for overview sparklines)
        self._global_minutes: Dict[int, MinuteBucket] = {}
        # admin snapshots
        self.cachestats = AdminSnapshot()
        self.servicestats = AdminSnapshot()
        self.activerequests = AdminSnapshot()
        self.lastrequests = AdminSnapshot()
        # recent log lines (raw) for the Logs panel
        self.recent_lines: Deque[str] = deque(maxlen=2000)
        # status: data source health
        self.logtail_status: str = "(starting)"
        self.admin_status: str = "(starting)"

    # -- log updates --------------------------------------------------------

    def record_request(
        self,
        ts: float,
        url: str,
        dur_ms: float,
        nbytes: int,
        status: int,
        apikey: str,
    ) -> None:
        with self._lock:
            u = self.urls.get(url)
            if u is None:
                u = UrlStats(url=url)
                self.urls[url] = u
            u.record(ts, dur_ms, nbytes, status, apikey)

            self.total_requests += 1
            self.total_bytes += nbytes
            if status >= 400:
                self.total_errors += 1

            # global per-minute aggregates
            m = int(ts // 60)
            g = self._global_minutes.get(m)
            if g is None:
                g = MinuteBucket()
                self._global_minutes[m] = g
                cutoff = m - HISTORY_MINUTES
                for k in list(self._global_minutes.keys()):
                    if k < cutoff:
                        del self._global_minutes[k]
            g.hist.add(dur_ms)
            g.count += 1
            g.bytes += nbytes
            if status >= 400:
                g.errors += 1
            g.status_counts[status] = g.status_counts.get(status, 0) + 1

    def record_raw_line(self, line: str) -> None:
        with self._lock:
            self.recent_lines.append(line)

    # -- readers ------------------------------------------------------------

    def snapshot_urls(
        self, window_min: int
    ) -> List[Tuple[str, MinuteBucket]]:
        """Return (url, merged-bucket) for every URL, for the given window."""
        with self._lock:
            out = []
            for url, u in self.urls.items():
                w = u.window(window_min)
                if w.count > 0:
                    out.append((url, w))
            return out

    def global_series(self, minutes: int, metric: str) -> List[float]:
        """Per-minute global series."""
        with self._lock:
            now_min = int(time.time() // 60)
            out: List[float] = []
            for m in range(now_min - minutes + 1, now_min + 1):
                b = self._global_minutes.get(m)
                if b is None or b.count == 0:
                    out.append(0.0)
                    continue
                if metric == "count":
                    out.append(float(b.count))
                elif metric == "mean_ms":
                    out.append(b.hist.mean())
                elif metric == "p95_ms":
                    out.append(b.hist.p95())
                elif metric == "bytes":
                    out.append(float(b.bytes))
                elif metric == "err_pct":
                    out.append(b.errors / b.count * 100.0 if b.count else 0.0)
                else:
                    out.append(0.0)
            return out

    def global_window(self, minutes: int) -> MinuteBucket:
        with self._lock:
            now_min = int(time.time() // 60)
            merged = MinuteBucket()
            for m in range(now_min - minutes + 1, now_min + 1):
                b = self._global_minutes.get(m)
                if b is None:
                    continue
                merged.hist.merge(b.hist)
                merged.count += b.count
                merged.bytes += b.bytes
                merged.errors += b.errors
                for s, c in b.status_counts.items():
                    merged.status_counts[s] = merged.status_counts.get(s, 0) + c
            return merged

    def url_detail(self, url: str) -> Optional[UrlStats]:
        with self._lock:
            return self.urls.get(url)
