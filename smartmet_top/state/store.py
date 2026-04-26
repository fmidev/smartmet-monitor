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
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from .histogram import Histogram

# Mutable retention window for minute-bucketed history. Defaults to
# 24 hours; raise via `set_history_minutes()` (the smtop CLI exposes
# this as `--history-minutes`) to keep a week. Memory grows roughly
# linearly: ~600 B per source per minute × 20 sources ≈ 12 KB per
# minute, so 24 h ≈ 17 MB and 7 days ≈ 120 MB.
HISTORY_MINUTES = 1440


def set_history_minutes(n: int) -> None:
    """Set the per-minute bucket retention. Affects every Store created
    after this call AND any in-flight pruning, since record() reads the
    module-level HISTORY_MINUTES on each insert."""
    global HISTORY_MINUTES
    HISTORY_MINUTES = max(1, int(n))


# Admin snapshots: keep ~5 minutes of 2-second polls = 150 samples.
# The actual cap is independent of poll interval; the deque just bounds
# memory so a long-running instance doesn't grow without limit.
ADMIN_HISTORY_SAMPLES = 300
# Per-process memory/IO samples at 2-second cadence: 1800 = 60 min of
# history, ~360 KB per PID. Plenty for the rolling sparklines.
PROC_HISTORY_SAMPLES = 1800
# Per-source per-second history depth — drives the live Plugins panel.
# 60 seconds × histogram (~40 ints each) × 20 sources ≈ 50 KB. Cheap.
HISTORY_SECONDS = 60


@dataclass
class MinuteBucket:
    hist: Histogram = field(default_factory=Histogram)
    count: int = 0
    bytes: int = 0
    errors: int = 0
    status_counts: Dict[int, int] = field(default_factory=dict)


@dataclass
class SecondBucket:
    """Same shape as MinuteBucket but bucketed at 1 s resolution."""

    hist: Histogram = field(default_factory=Histogram)
    count: int = 0
    bytes: int = 0
    errors: int = 0


@dataclass
class SourceStats:
    """Per-access-log-file rolling stats: 60 s of second-buckets for the
    "live" view, plus 60 min of minute-buckets for parity with the
    URLs/Keys panels."""

    label: str
    second_buckets: Dict[int, SecondBucket] = field(default_factory=dict)
    minute_buckets: Dict[int, MinuteBucket] = field(default_factory=dict)
    last_seen: float = 0.0

    def record(self, ts: float, dur_ms: float, nbytes: int, status: int) -> None:
        s = int(ts)
        sb = self.second_buckets.get(s)
        if sb is None:
            sb = SecondBucket()
            self.second_buckets[s] = sb
            cutoff = s - HISTORY_SECONDS
            for k in list(self.second_buckets.keys()):
                if k < cutoff:
                    del self.second_buckets[k]
        sb.hist.add(dur_ms)
        sb.count += 1
        sb.bytes += nbytes
        if status >= 400:
            sb.errors += 1

        m = int(ts // 60)
        mb = self.minute_buckets.get(m)
        if mb is None:
            mb = MinuteBucket()
            self.minute_buckets[m] = mb
            cutoff_m = m - HISTORY_MINUTES
            for k in list(self.minute_buckets.keys()):
                if k < cutoff_m:
                    del self.minute_buckets[k]
        mb.hist.add(dur_ms)
        mb.count += 1
        mb.bytes += nbytes
        if status >= 400:
            mb.errors += 1
        mb.status_counts[status] = mb.status_counts.get(status, 0) + 1
        self.last_seen = ts

    def second_window(self, seconds: int) -> SecondBucket:
        now_s = int(time.time())
        merged = SecondBucket()
        for s in range(now_s - seconds + 1, now_s + 1):
            b = self.second_buckets.get(s)
            if b is None:
                continue
            merged.hist.merge(b.hist)
            merged.count += b.count
            merged.bytes += b.bytes
            merged.errors += b.errors
        return merged

    def second_series(self, metric: str, seconds: int = HISTORY_SECONDS) -> List[float]:
        """Per-second values for the last `seconds`. Oldest first."""
        now_s = int(time.time())
        out: List[float] = []
        for s in range(now_s - seconds + 1, now_s + 1):
            b = self.second_buckets.get(s)
            if b is None or b.count == 0:
                out.append(0.0)
                continue
            if metric == "count":  # requests in this second
                out.append(float(b.count))
            elif metric == "mean_ms":
                out.append(b.hist.mean())
            elif metric == "p95_ms":
                out.append(b.hist.p95())
            elif metric == "max_ms":
                out.append(b.hist.max_ms)
            elif metric == "bytes":  # total bytes in this second (≈ B/s)
                out.append(float(b.bytes))
            elif metric == "bytes_mean":  # mean response size in this second
                out.append(float(b.bytes) / b.count if b.count else 0.0)
            elif metric == "err_pct":
                out.append(b.errors / b.count * 100.0 if b.count else 0.0)
            else:
                out.append(0.0)
        return out

    def minute_window(self, minutes: int) -> MinuteBucket:
        """Merge the last N minute-buckets into one. Counterpart of
        second_window() at coarser resolution — readable after --replay
        because minute_buckets get backfilled by historical log lines."""
        now_min = int(time.time() // 60)
        merged = MinuteBucket()
        for m in range(now_min - minutes + 1, now_min + 1):
            b = self.minute_buckets.get(m)
            if b is None:
                continue
            merged.hist.merge(b.hist)
            merged.count += b.count
            merged.bytes += b.bytes
            merged.errors += b.errors
            for k, v in b.status_counts.items():
                merged.status_counts[k] = merged.status_counts.get(k, 0) + v
        return merged

    def minute_series(self, metric: str, minutes: int) -> List[float]:
        """Per-minute values for the last N minutes. Oldest first."""
        now_min = int(time.time() // 60)
        out: List[float] = []
        for m in range(now_min - minutes + 1, now_min + 1):
            b = self.minute_buckets.get(m)
            if b is None or b.count == 0:
                out.append(0.0)
                continue
            if metric == "count":
                # Same semantics as second_series 'count' but per minute —
                # divide by 60 if the panel wants req/s units.
                out.append(float(b.count))
            elif metric == "mean_ms":
                out.append(b.hist.mean())
            elif metric == "p95_ms":
                out.append(b.hist.p95())
            elif metric == "max_ms":
                out.append(b.hist.max_ms)
            elif metric == "bytes":
                out.append(float(b.bytes))
            elif metric == "bytes_mean":
                out.append(float(b.bytes) / b.count if b.count else 0.0)
            elif metric == "err_pct":
                out.append(b.errors / b.count * 100.0 if b.count else 0.0)
            else:
                out.append(0.0)
        return out


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
class ProcSample:
    """One polling tick of cheap /proc counters for a single PID."""

    ts: float = 0.0
    vm_rss_kb: int = 0
    vm_size_kb: int = 0
    vm_swap_kb: int = 0
    vm_pte_kb: int = 0
    vm_hwm_kb: int = 0
    rss_anon_kb: int = 0
    rss_file_kb: int = 0
    rss_shmem_kb: int = 0
    threads: int = 0
    io_read_bytes: int = 0
    io_write_bytes: int = 0
    fds: int = 0


@dataclass
class PerfData:
    """Aggregated perf samples for one PID.

    `minute_buckets[minute_epoch][symbol] = sample_count` — the bucketing
    matches the access-log Histogram so the perf time series renders the
    same way as URL latency. `recent_stacks` is a bounded ring of the
    most recent full call stacks (root → leaf), used by the flamegraph
    view; older stacks fall off automatically.
    """

    pid: int
    minute_buckets: Dict[int, Dict[str, int]] = field(default_factory=dict)
    recent_stacks: Deque[Tuple[str, ...]] = field(
        default_factory=lambda: deque(maxlen=20000)
    )
    last_sample_ts: float = 0.0
    last_sample_count: int = 0


@dataclass
class ProcInfo:
    """Static metadata + ring of samples + last on-demand smaps_rollup."""

    pid: int
    cmdline: str = ""
    role: str = "unknown"
    started_at: float = 0.0
    samples: Deque[ProcSample] = field(
        default_factory=lambda: deque(maxlen=PROC_HISTORY_SAMPLES)
    )
    rollup: Dict[str, int] = field(default_factory=dict)
    rollup_ts: float = 0.0


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
        # Per-host history of "active request count" — one sample per
        # admin poll. Drives the sparkline at the top of the Active
        # panel so the operator can see the in-flight load trending.
        # Bounded with the same ADMIN_HISTORY_SAMPLES cap as the other
        # admin sparklines.
        self.active_count_history: Dict[str, Deque[int]] = {}
        # auto-detected endpoint availability & role per host
        self.available_what: Dict[str, set] = {}
        self.host_role: Dict[str, str] = {}  # "frontend", "backend", or "unknown"
        # Recent log lines (raw, with [plugin] prefix) for the Logs
        # panel's "all" view that interleaves every tailed file.
        self.recent_lines: Deque[str] = deque(maxlen=20000)
        # Per-source recent lines so the Logs panel can show a single
        # plugin's tail without it being flushed out by a high-rate
        # plugin's traffic. Each source has its own bounded ring; idle
        # plugins keep their last 2000 lines forever, which is what
        # makes "switch sources with arrow keys" actually useful.
        self.source_lines: Dict[str, Deque[str]] = {}
        # status: data source health
        self.logtail_status: str = "(starting)"
        self.admin_status: Dict[str, str] = {}
        # Cross-panel "drill into another panel" request. A panel sets
        # this to (target_hotkey, params_dict) when the operator drills
        # in (e.g. Plugins → URLs filtered by plugin label); the App
        # consumes and clears it after each key event.
        self.pending_panel_switch: Optional[Tuple[str, Dict[str, str]]] = None
        # /proc-derived per-PID stats for smartmetd processes
        self.procs: Dict[int, ProcInfo] = {}
        # Per-access-log-file (per-plugin) live stats. Key is the log
        # basename minus the trailing "-access-log" suffix — e.g. "wms",
        # "timeseries", "wfs". The Plugins panel reads from here.
        self.source_stats: Dict[str, SourceStats] = {}
        # Which PID the operator is focused on. Source of truth for both
        # the ProcPanel and the (optional) perf sampler — when the user
        # switches PIDs the perf loop re-targets automatically.
        self.selected_proc_pid: Optional[int] = None
        # Perf-top sampler state (only populated when smtop is run with
        # --perf, which gates the sampler from spawning at all).
        self.perfdata: Dict[int, PerfData] = {}
        self.perf_enabled: bool = False
        self.perf_status: str = "(disabled — start smtop with --perf)"
        # Full diagnostic text from the most recent failed perf record /
        # script invocation. Cleared on the next successful cycle.
        self.perf_last_error: str = ""
        self.perf_target_pid: Optional[int] = None
        # Per-cycle perf record duration in seconds. Initialised from
        # the --perf-record-seconds CLI flag and mutable from the Flame
        # view's `s`-keyed selection overlay; perf_loop reads it on
        # each iteration so changes take effect on the next cycle.
        self.perf_record_seconds: int = 3

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
            self.active_count_history.setdefault(
                host, deque(maxlen=ADMIN_HISTORY_SAMPLES)
            )
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
        source_label: Optional[str] = None,
    ) -> None:
        with self._lock:
            if source_label is not None:
                src = self.source_stats.get(source_label)
                if src is None:
                    src = SourceStats(label=source_label)
                    self.source_stats[source_label] = src
                src.record(ts, dur_ms, nbytes, status)

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

    def record_raw_line(self, line: str, source: str = "") -> None:
        """Append a raw access-log line to the recent rings.

        Stored both in the global merged ring (with a `[<plugin>]`
        prefix so the source is visible in the merged view) and in
        the per-source ring (raw, without the prefix — the source is
        already implied by the ring it's in). The Logs panel uses
        whichever the operator selected.
        """
        with self._lock:
            if source:
                self.recent_lines.append(f"[{source}] {line}")
                buf = self.source_lines.get(source)
                if buf is None:
                    buf = deque(maxlen=2000)
                    self.source_lines[source] = buf
                buf.append(line)
            else:
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

    # -- per-source (per-access-log-file) -----------------------------------

    def register_source(self, label: str) -> None:
        """Register a log file's source label so the Plugins panel shows it
        even before the first request lands."""
        with self._lock:
            if label not in self.source_stats:
                self.source_stats[label] = SourceStats(label=label)

    def snapshot_sources(self) -> List[SourceStats]:
        with self._lock:
            return sorted(self.source_stats.values(), key=lambda s: s.label)

    def source_detail(self, label: str) -> Optional[SourceStats]:
        with self._lock:
            return self.source_stats.get(label)

    # -- /proc-derived stats ------------------------------------------------

    def proc_register(self, pid: int, cmdline: str = "", role: str = "unknown",
                      started_at: float = 0.0) -> None:
        with self._lock:
            info = self.procs.get(pid)
            if info is None:
                self.procs[pid] = ProcInfo(
                    pid=pid, cmdline=cmdline, role=role, started_at=started_at
                )
            else:
                # PID may be re-registered with refined info after discovery.
                info.cmdline = cmdline or info.cmdline
                info.role = role or info.role
                info.started_at = started_at or info.started_at

    def proc_update(self, pid: int, ts: float, **fields) -> None:
        with self._lock:
            info = self.procs.get(pid)
            if info is None:
                info = ProcInfo(pid=pid)
                self.procs[pid] = info
            sample = ProcSample(ts=ts, **fields)
            info.samples.append(sample)

    def proc_remove(self, pid: int) -> None:
        with self._lock:
            self.procs.pop(pid, None)

    def proc_list(self) -> List[ProcInfo]:
        with self._lock:
            return sorted(self.procs.values(), key=lambda p: p.pid)

    def proc_latest(self, pid: int) -> Optional[ProcSample]:
        with self._lock:
            info = self.procs.get(pid)
            if info is None or not info.samples:
                return None
            return info.samples[-1]

    def proc_series(self, pid: int, field_name: str,
                    samples: int = 0) -> List[float]:
        """Return per-tick values for `field_name` across the most recent
        `samples` ticks (or the whole history if `samples == 0`)."""
        with self._lock:
            info = self.procs.get(pid)
            if info is None or not info.samples:
                return []
            data = list(info.samples)
            if samples > 0:
                data = data[-samples:]
            return [float(getattr(s, field_name, 0)) for s in data]

    def proc_set_rollup(self, pid: int, rollup: Dict[str, int],
                        ts: float) -> None:
        with self._lock:
            info = self.procs.get(pid)
            if info is None:
                return
            info.rollup = dict(rollup)
            info.rollup_ts = ts

    def proc_select(self, pid: Optional[int]) -> None:
        with self._lock:
            self.selected_proc_pid = pid

    def proc_selected(self) -> Optional[int]:
        with self._lock:
            pid = self.selected_proc_pid
            if pid is not None and pid in self.procs:
                return pid
            # Fall back to the lowest-PID smartmetd if the saved one is gone.
            if self.procs:
                pid = min(self.procs.keys())
                self.selected_proc_pid = pid
                return pid
            return None

    # -- perf data ----------------------------------------------------------

    def perf_record_samples(self, pid: int, ts: float,
                            stacks: Iterable[Tuple[str, ...]]) -> None:
        """Aggregate a batch of stacks: per-minute leaf-symbol counts plus
        a copy on the bounded `recent_stacks` ring for flamegraph view."""
        with self._lock:
            pd = self.perfdata.get(pid)
            if pd is None:
                pd = PerfData(pid=pid)
                self.perfdata[pid] = pd
            m = int(ts // 60)
            bucket = pd.minute_buckets.get(m)
            if bucket is None:
                bucket = {}
                pd.minute_buckets[m] = bucket
                cutoff = m - HISTORY_MINUTES
                for k in list(pd.minute_buckets.keys()):
                    if k < cutoff:
                        del pd.minute_buckets[k]
            n = 0
            for stack in stacks:
                if not stack:
                    continue
                leaf = stack[-1]
                bucket[leaf] = bucket.get(leaf, 0) + 1
                pd.recent_stacks.append(stack)
                n += 1
            pd.last_sample_ts = ts
            pd.last_sample_count = n

    def perf_top_symbols(self, pid: int, minutes: int = 10,
                         n: int = 20) -> List[Tuple[str, int]]:
        with self._lock:
            pd = self.perfdata.get(pid)
            if pd is None:
                return []
            now_min = int(time.time() // 60)
            merged: Dict[str, int] = {}
            for m in range(now_min - minutes + 1, now_min + 1):
                for sym, c in pd.minute_buckets.get(m, {}).items():
                    merged[sym] = merged.get(sym, 0) + c
            return sorted(merged.items(), key=lambda x: -x[1])[:n]

    def perf_symbol_series(self, pid: int, symbol: str,
                           minutes: int = 20) -> List[float]:
        with self._lock:
            pd = self.perfdata.get(pid)
            if pd is None:
                return []
            now_min = int(time.time() // 60)
            return [
                float(pd.minute_buckets.get(m, {}).get(symbol, 0))
                for m in range(now_min - minutes + 1, now_min + 1)
            ]

    def perf_recent_stacks(self, pid: int) -> List[Tuple[str, ...]]:
        with self._lock:
            pd = self.perfdata.get(pid)
            if pd is None:
                return []
            return list(pd.recent_stacks)

    def perf_last_sample_count(self, pid: int) -> int:
        with self._lock:
            pd = self.perfdata.get(pid)
            return pd.last_sample_count if pd else 0
