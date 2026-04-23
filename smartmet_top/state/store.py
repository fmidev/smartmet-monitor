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
# Admin snapshots: keep ~5 minutes of 2-second polls = 150 samples.
# The actual cap is independent of poll interval; the deque just bounds
# memory so a long-running instance doesn't grow without limit.
ADMIN_HISTORY_SAMPLES = 300


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


@dataclass
class SeriesPoint:
    ts: float
    values: Dict[str, float] = field(default_factory=dict)


class HistorySeries:
    """Bounded ring buffer of SeriesPoints keyed by entity name."""

    __slots__ = ("_data",)

    def __init__(self) -> None:
        self._data: Dict[str, Deque[SeriesPoint]] = {}

    def append(self, name: str, ts: float, values: Dict[str, float]) -> None:
        dq = self._data.get(name)
        if dq is None:
            dq = deque(maxlen=ADMIN_HISTORY_SAMPLES)
            self._data[name] = dq
        dq.append(SeriesPoint(ts=ts, values=dict(values)))

    def series(self, name: str, key: str, samples: int = 0) -> List[float]:
        dq = self._data.get(name)
        if not dq:
            return []
        data = list(dq)
        if samples > 0:
            data = data[-samples:]
        return [p.values.get(key, 0.0) for p in data]

    def names(self) -> List[str]:
        return list(self._data.keys())

    def prune(self, keep: set) -> None:
        for k in list(self._data.keys()):
            if k not in keep:
                del self._data[k]


class Store:
    """Thread-safe store for URL stats and admin-plugin snapshots."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.urls: Dict[str, UrlStats] = {}
        # per API key — UrlStats whose .url is the key. apikey_counts on
        # the per-key object stores url→count (the drill-in view uses it).
        self.keys: Dict[str, UrlStats] = {}
        self.total_requests: int = 0
        self.total_bytes: int = 0
        self.total_errors: int = 0
        # per-minute global metrics (for overview sparklines)
        self._global_minutes: Dict[int, MinuteBucket] = {}
        # admin snapshots keyed by host label (so the UI can show all hosts).
        # The "default" / single-host case still works — it just lives at
        # host=<the one label>.
        self.admin_hosts: List[str] = []
        self.cachestats: Dict[str, AdminSnapshot] = {}
        self.servicestats: Dict[str, AdminSnapshot] = {}
        self.activerequests: Dict[str, AdminSnapshot] = {}
        self.lastrequests: Dict[str, AdminSnapshot] = {}
        # per-entity historical series (for sparklines), per host
        self.cache_history: Dict[str, HistorySeries] = {}
        self.service_history: Dict[str, HistorySeries] = {}
        # auto-detected endpoint availability & role per host
        self.available_what: Dict[str, set] = {}
        self.host_role: Dict[str, str] = {}  # "frontend", "backend", or "unknown"
        # recent log lines (raw) for the Logs panel
        self.recent_lines: Deque[str] = deque(maxlen=2000)
        # status: data source health
        self.logtail_status: str = "(starting)"
        self.admin_status: Dict[str, str] = {}

    def register_admin_host(self, host: str) -> None:
        with self._lock:
            if host in self.admin_hosts:
                return
            self.admin_hosts.append(host)
            self.cachestats.setdefault(host, AdminSnapshot())
            self.servicestats.setdefault(host, AdminSnapshot())
            self.activerequests.setdefault(host, AdminSnapshot())
            self.lastrequests.setdefault(host, AdminSnapshot())
            self.cache_history.setdefault(host, HistorySeries())
            self.service_history.setdefault(host, HistorySeries())
            self.admin_status.setdefault(host, "(starting)")
            self.available_what.setdefault(host, set())
            self.host_role.setdefault(host, "unknown")

    def hosts_supporting(self, what: str) -> List[str]:
        """Which configured hosts have declared support for a given what=?"""
        with self._lock:
            return [h for h in self.admin_hosts
                    if not self.available_what.get(h) or what in self.available_what[h]]

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

            # per-API-key aggregate reuses UrlStats with the key name as
            # the identifier and counts the URLs that key hit.
            ak = apikey or "-"
            k = self.keys.get(ak)
            if k is None:
                k = UrlStats(url=ak)
                self.keys[ak] = k
            k.record(ts, dur_ms, nbytes, status, apikey)
            # accumulate URLs-per-key for the drill-in view
            k.apikey_counts[url] = k.apikey_counts.get(url, 0) + 1

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

    def snapshot_keys(self, window_min: int) -> List[Tuple[str, MinuteBucket]]:
        with self._lock:
            out = []
            for k, ks in self.keys.items():
                w = ks.window(window_min)
                if w.count > 0:
                    out.append((k, w))
            return out

    def key_detail(self, apikey: str) -> Optional[UrlStats]:
        with self._lock:
            return self.keys.get(apikey)
