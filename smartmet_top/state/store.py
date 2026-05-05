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

from .alerts import Alert, STALE_AFTER_SECONDS
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
    # Cumulative major page-fault counter (/proc/PID/stat field 12).
    # Rate is computed by the panel as delta-per-second across samples,
    # so storing the cumulative value keeps the math identical to how
    # io_read_bytes / io_write_bytes are turned into rates.
    majflt: int = 0
    # Cumulative CPU jiffies (/proc/PID/stat fields 14, 15). Divide
    # delta by SC_CLK_TCK and the wall-clock sample interval to get
    # "fraction of one core" used between samples.
    utime: int = 0
    stime: int = 0


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
class OffCpuData:
    """Aggregated off-CPU samples for one PID.

    Different from PerfData: each entry in `recent_stacks` is a
    (stack, microseconds) pair rather than a bare stack, because
    off-CPU profiling weights stacks by *time spent blocked* rather
    than sample-count. The flame view multiplies counts by these
    weights when building the tree.

    Bounded at 4000 entries (vs 20 000 for on-CPU): off-CPU output is
    aggregated by the bcc tool already, so a single recording yields
    one entry per unique (thread, stack), typically dozens not
    thousands. 4000 holds many recording cycles without churn.
    """

    pid: int
    recent_stacks: Deque[Tuple[Tuple[str, ...], int]] = field(
        default_factory=lambda: deque(maxlen=4000)
    )
    last_sample_ts: float = 0.0
    last_total_us: int = 0


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
        # IP-flow retention: per minute, raw request tuples keyed by
        # client IP. Used by the IPFlow panel to animate requests as
        # particles flying from each IP toward the centre. Stored as
        # tuples (not dataclasses) so a busy backend with 100k requests
        # per day costs ~16 MB at most: ~160 B per tuple including
        # CPython overhead. Tuple shape:
        # ``(ts, ip, dur_ms, nbytes, status, source_label)`` —
        # source_label is the access-log source name (e.g. "wms",
        # "timeseries"), interned by CPython so the per-record cost
        # of the extra string is just a pointer. "" when the caller
        # didn't supply one. Pruned by the same HISTORY_MINUTES
        # window as everything else.
        self._ipflow_minutes: Dict[
            int, List[Tuple[float, str, int, int, int, str]]
        ] = {}
        # admin snapshots keyed by host label (so the UI can show all hosts).
        # The "default" / single-host case still works — it just lives at
        # host=<the one label>.
        self.admin_hosts: List[str] = []
        self.cachestats: Dict[str, AdminSnapshot] = {}
        self.servicestats: Dict[str, AdminSnapshot] = {}
        self.activerequests: Dict[str, AdminSnapshot] = {}
        self.lastrequests: Dict[str, AdminSnapshot] = {}
        # Allocator-stats history per host. The latest sample drives
        # the Heap section's current numbers; the bounded ring backs
        # the sparkline. 30 samples × 30 s polling = 15 minutes of
        # history visible at one zoom level. Polled less frequently
        # than the other admin endpoints because the JSON dump is
        # large (~50 KB on a 32-arena backend) and the numbers don't
        # change at sub-second rates. Type is forward-string-quoted to
        # avoid a circular import (sources/mallocstats.py imports
        # store.MallocSample for typing).
        self.mallocstats_latest: Dict[str, "MallocSample"] = {}
        self.mallocstats_history: Dict[str, Deque["MallocSample"]] = {}
        self.mallocstats_error: Dict[str, str] = {}
        self.mallocstats_fetched_at: Dict[str, float] = {}
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
        # Replay progress — populated by runtime.replay_logs while a
        # bulk_load is in flight so the dashboard can render a
        # "processing logs…" banner instead of empty panels. Always a
        # dict so handlers can read it without isinstance checks.
        self.replay_status: Dict[str, object] = {"in_progress": False}
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
        # Global freeze switch read by every recorder loop (perf,
        # offcpu, pagefault, wakeup, blockflame, malloc). Set by the
        # Flame view's `a` (analyze) overlay so the captured stack
        # rings stay frozen while the operator studies the findings;
        # cleared when the overlay is dismissed. Pause takes effect at
        # the next iteration boundary — worst-case lag is one
        # record_seconds cycle.
        self.profile_paused: bool = False
        # Off-CPU profiling state — populated when the off-CPU loop is
        # running. Probed lazily; if no backend (bcc-tools / perf-fallback)
        # is available, offcpu_enabled stays False and offcpu_status
        # carries the install hint to display in the Flame view.
        self.offcpu_data: Dict[int, OffCpuData] = {}
        self.offcpu_enabled: bool = False
        self.offcpu_status: str = "(off-CPU sampler not started)"
        self.offcpu_last_error: str = ""
        self.offcpu_target_pid: Optional[int] = None
        self.offcpu_backend: str = ""  # 'bcc' | 'perf' | ''
        # Block-I/O latency sampler state. Host-wide (bcc biolatency
        # operates at the block layer, not per-PID); on a dedicated
        # SmartMet host the dominant block I/O is smartmetd anyway.
        # Each entry: (ts, p50_us, p95_us, p99_us, total_ops_in_window).
        # Bounded ring of 120 entries = 10 minutes at the default 5 s
        # cycle, which fills a 60-cell sparkline twice over.
        self.biolat_samples: Deque[Tuple[float, int, int, int, int]] = deque(maxlen=120)
        self.biolat_enabled: bool = False
        self.biolat_status: str = "(block-I/O sampler not started)"
        self.biolat_last_error: str = ""
        # Network counters from /proc/net/{snmp,netstat,dev}. Always
        # available — no eBPF dependency, runs everywhere Linux runs.
        # TCP entries: (ts, retrans/s, listen_overflows/s, listen_drops/s).
        # NIC entries are per-interface deques of (ts, rx_bytes/s, tx_bytes/s).
        self.netstats_tcp: Deque[Tuple[float, float, float, float]] = deque(maxlen=180)
        self.netstats_iface: Dict[str, Deque[Tuple[float, float, float]]] = {}
        # TCP connection state distribution from /proc/net/tcp{,6}.
        # Each entry: (ts, {state_name: count}, [(listen_port, recv_q), …]).
        self.netstats_states: Deque[Tuple[float, Dict[str, int], List[Tuple[int, int]]]] = deque(maxlen=180)
        self.netstats_enabled: bool = False
        self.netstats_status: str = "(network sampler not started)"
        # Run-queue latency sampler (bcc-tools' runqlat-bpfcc).
        # Same shape as biolat — (ts, p50_us, p95_us, p99_us, total).
        # Operationally most valuable on virtualised hosts where CFS
        # throttling or noisy neighbours hold ready threads off CPU.
        self.runqlat_samples: Deque[Tuple[float, int, int, int, int]] = deque(maxlen=120)
        self.runqlat_enabled: bool = False
        self.runqlat_status: str = "(runqlat sampler not started)"
        self.runqlat_last_error: str = ""
        # perf stat (hardware PMU counters) — IPC + cache/branch miss
        # rates per smartmetd PID. Each sample: (ts, pid, ipc,
        # cache_miss_rate, branch_miss_rate). Bounded ring of 60
        # samples = 10 minutes at the default 10 s cycle.
        self.perfstat_samples: Deque[Tuple[float, int, float, float, float]] = deque(maxlen=60)
        # Latest raw counter values per metric (for the panel header).
        self.perfstat_counters: Dict[str, int] = {}
        self.perfstat_enabled: bool = False
        self.perfstat_status: str = "(perfstat sampler not started)"
        self.perfstat_last_error: str = ""
        # Cross-panel alerts. Detectors in each source upsert into this
        # dict by stable `id`; the UI (tab-bar badge, per-panel banner,
        # `!`-overlay) reads from it on every redraw. See state/alerts.py
        # for the full lifecycle description.
        self.alerts: Dict[str, Alert] = {}
        # systemd-journal tail (set by journal_loop; rendered as a
        # [journal] source in the Logs panel).
        self.journal_enabled: bool = False
        self.journal_status: str = "(journal tail not started)"
        # Major page-fault stack sampler — perf record -e major-faults.
        # Same shape as perfdata: per-PID dict, each entry holding a
        # bounded ring of recent stacks. Stacks are unweighted (one
        # sample = one fault), so the flame tree builder just counts.
        self.pagefault_data: Dict[int, "PerfData"] = {}
        self.pagefault_enabled: bool = False
        self.pagefault_status: str = "(page-fault sampler not started)"
        self.pagefault_last_error: str = ""
        # Wakeup stack sampler — perf record -e sched:sched_wakeup.
        # Each sample is one wakeup event; flame width measures
        # "this code path generated N wakeups for the focused PID".
        self.wakeup_data: Dict[int, "PerfData"] = {}
        self.wakeup_enabled: bool = False
        self.wakeup_status: str = "(wakeup sampler not started)"
        self.wakeup_last_error: str = ""
        # Block-I/O issue stack sampler — perf record -e
        # block:block_rq_issue. Each sample is one block-layer
        # request issued; flame width measures "this code path
        # issued N block requests".
        self.blockflame_data: Dict[int, "PerfData"] = {}
        self.blockflame_enabled: bool = False
        self.blockflame_status: str = "(block-I/O sampler not started)"
        self.blockflame_last_error: str = ""
        # Allocation stack sampler — bpftrace uprobe on malloc.
        # Stacks are weighted by total bytes allocated. Off by
        # default; only runs when smtop is started with
        # --malloc-flame because the uprobe overhead can be heavy.
        self.malloc_data: Dict[int, "OffCpuData"] = {}
        self.malloc_enabled: bool = False
        self.malloc_status: str = "(malloc flame not started; pass --malloc-flame to enable)"
        self.malloc_last_error: str = ""
        self.malloc_allocator: str = ""
        # Page-cache and memory-reclaim stats from /proc/vmstat +
        # /proc/meminfo. Always-on; no external tools required.
        # Each entry: (ts, majflt_rate, kswapd_rate, direct_rate,
        # scan_rate, cache_kb, mem_total_kb, mem_avail_kb).
        # 120 entries × 5 s = 10 min of history at default cycle.
        self.vmstats_samples: Deque[Tuple[float, float, float, float, float, int, int, int]] = deque(maxlen=120)
        self.vmstats_enabled: bool = False
        self.vmstats_status: str = "(vmstats sampler not started)"

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
            self.mallocstats_history.setdefault(host, deque(maxlen=120))
            self.mallocstats_error.setdefault(host, "")
            self.mallocstats_fetched_at.setdefault(host, 0.0)

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
        ip: str = "",
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

            # IP-flow retention. Skip when no IP — keeps existing test
            # paths and snapshot replay (which doesn't carry an IP)
            # from paying the per-request tuple cost.
            if ip:
                bucket = self._ipflow_minutes.get(m)
                if bucket is None:
                    bucket = []
                    self._ipflow_minutes[m] = bucket
                    cutoff = m - HISTORY_MINUTES
                    for k in list(self._ipflow_minutes.keys()):
                        if k < cutoff:
                            del self._ipflow_minutes[k]
                bucket.append((ts, ip, int(dur_ms), int(nbytes),
                                int(status), source_label or ""))

    def ipflow_timeline(
        self,
        minutes: int = HISTORY_MINUTES,
        source: Optional[str] = None,
    ) -> List[Tuple[int, int, int]]:
        """Per-minute aggregate (count, bytes) over the last `minutes`
        of retained history. Returned newest-last so the chart's X
        axis runs left-to-right in time order.

        With ``source=None`` reads from ``_global_minutes`` (always
        populated, every request whether IP-tagged or not). With a
        named source it reads from ``source_stats[source].minute_buckets``,
        which the access-log tail populates directly via
        ``SourceStats.record``."""
        with self._lock:
            if source:
                src = self.source_stats.get(source)
                if src is None or not src.minute_buckets:
                    return []
                buckets = src.minute_buckets
            else:
                if not self._global_minutes:
                    return []
                buckets = self._global_minutes
            latest = max(buckets)
            cutoff = latest - max(1, int(minutes)) + 1
            out: List[Tuple[int, int, int]] = []
            for m in sorted(buckets):
                if m < cutoff:
                    continue
                b = buckets[m]
                out.append((m * 60, int(b.count), int(b.bytes)))
            return out

    def ipflow_sources(self) -> List[str]:
        """List of access-log source labels with at least one
        recorded request, used to populate the IP Flow panel's
        source-filter dropdown. Filters by ``last_seen`` so a
        source that was registered (via ``tail_many``) but has not
        yet seen traffic stays out of the list — the dropdown only
        offers options that would actually return data."""
        with self._lock:
            return sorted(
                lbl for lbl, s in self.source_stats.items()
                if s.last_seen > 0)

    def ipflow_window(
        self,
        start_ts: float,
        seconds: float,
        top_n: int = 0,
        source: Optional[str] = None,
    ) -> Tuple[List[Tuple[float, str, int, int, int, str]], Dict[str, Tuple[int, int]]]:
        """Return raw IP-flow records intersecting [start_ts, start_ts+seconds]
        plus a per-IP `(count, bytes)` summary for the same window.

        When `top_n > 0`, requests for IPs outside the top-N busiest
        (by request count) are dropped from the records list — the
        summary still includes every IP so the panel can show the
        long-tail count without rendering it as particles.

        With ``source`` set, only records whose source_label matches
        the filter are returned (and contribute to the summary). The
        empty string and ``None`` both mean "all sources"."""
        with self._lock:
            end_ts = start_ts + max(0.0, seconds)
            m_start = int(start_ts // 60)
            m_end = int(end_ts // 60)
            want_source = source or None
            recs: List[Tuple[float, str, int, int, int, str]] = []
            ip_summary: Dict[str, List[int]] = {}
            for m in range(m_start, m_end + 1):
                bucket = self._ipflow_minutes.get(m)
                if bucket is None:
                    continue
                for rec in bucket:
                    ts, ip, dur, nb, st, src = rec
                    if ts < start_ts or ts > end_ts:
                        continue
                    if want_source is not None and src != want_source:
                        continue
                    recs.append(rec)
                    s = ip_summary.get(ip)
                    if s is None:
                        ip_summary[ip] = [1, nb]
                    else:
                        s[0] += 1
                        s[1] += nb
            recs.sort(key=lambda r: r[0])
            if top_n and len(ip_summary) > top_n:
                top = set(ip for ip, _ in sorted(
                    ip_summary.items(),
                    key=lambda kv: kv[1][0], reverse=True)[:top_n])
                recs = [r for r in recs if r[1] in top]
            summary = {ip: (c, b) for ip, (c, b) in ip_summary.items()}
            return recs, summary

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
            # Saved selection is gone (or never existed) — fall back to
            # the role-aware default.
            pid = self._proc_default_pid_locked()
            if pid is not None:
                self.selected_proc_pid = pid
            return pid

    def proc_default_pid(self) -> Optional[int]:
        """Best PID to focus on when the operator has not made an
        explicit choice. Prefers a smartmetd in the `backend` role
        over `frontend` / `mixed` / `unknown`, because the backend
        does the actual data work — it is what the operator almost
        always wants to profile. Falls back to the lowest PID when
        no backend is detected."""
        with self._lock:
            return self._proc_default_pid_locked()

    def _proc_default_pid_locked(self) -> Optional[int]:
        if not self.procs:
            return None
        # Backend wins if at least one is detected. Multiple backends
        # → pick the lowest PID for stable behaviour across restarts.
        backend = sorted(p.pid for p in self.procs.values()
                         if p.role == "backend")
        if backend:
            return backend[0]
        return min(self.procs.keys())

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

    # -- off-CPU data -------------------------------------------------------

    def offcpu_record_samples(
        self, pid: int, ts: float,
        stacks_with_us: Iterable[Tuple[Tuple[str, ...], int]],
    ) -> None:
        """Append a batch of (stack, microseconds) pairs to the off-CPU
        ring for `pid`. Each call corresponds to one recording cycle of
        offcputime-bpfcc."""
        with self._lock:
            od = self.offcpu_data.get(pid)
            if od is None:
                od = OffCpuData(pid=pid)
                self.offcpu_data[pid] = od
            total = 0
            for stack, us in stacks_with_us:
                if not stack or us <= 0:
                    continue
                od.recent_stacks.append((stack, us))
                total += us
            od.last_sample_ts = ts
            od.last_total_us = total

    def offcpu_recent_stacks(
        self, pid: int,
    ) -> List[Tuple[Tuple[str, ...], int]]:
        with self._lock:
            od = self.offcpu_data.get(pid)
            if od is None:
                return []
            return list(od.recent_stacks)

    def offcpu_last_total_us(self, pid: int) -> int:
        with self._lock:
            od = self.offcpu_data.get(pid)
            return od.last_total_us if od else 0

    # -- page-fault flame data ---------------------------------------------

    def pagefault_record_samples(self, pid: int, ts: float,
                                 stacks) -> None:
        """Append a batch of major-fault stacks to the per-PID ring.
        Each stack counts as one sample (one major fault); the flame
        view weights frames by occurrence."""
        with self._lock:
            pd = self.pagefault_data.get(pid)
            if pd is None:
                pd = PerfData(pid=pid)
                self.pagefault_data[pid] = pd
            n = 0
            for stack in stacks:
                if not stack:
                    continue
                pd.recent_stacks.append(stack)
                n += 1
            pd.last_sample_ts = ts
            pd.last_sample_count = n

    def pagefault_recent_stacks(self, pid: int):
        with self._lock:
            pd = self.pagefault_data.get(pid)
            if pd is None:
                return []
            return list(pd.recent_stacks)

    def pagefault_last_sample_count(self, pid: int) -> int:
        with self._lock:
            pd = self.pagefault_data.get(pid)
            return pd.last_sample_count if pd else 0

    # -- wakeup flame data --------------------------------------------------

    def wakeup_record_samples(self, pid: int, ts: float, stacks) -> None:
        with self._lock:
            pd = self.wakeup_data.get(pid)
            if pd is None:
                pd = PerfData(pid=pid)
                self.wakeup_data[pid] = pd
            n = 0
            for stack in stacks:
                if not stack:
                    continue
                pd.recent_stacks.append(stack)
                n += 1
            pd.last_sample_ts = ts
            pd.last_sample_count = n

    def wakeup_recent_stacks(self, pid: int):
        with self._lock:
            pd = self.wakeup_data.get(pid)
            if pd is None:
                return []
            return list(pd.recent_stacks)

    def wakeup_last_sample_count(self, pid: int) -> int:
        with self._lock:
            pd = self.wakeup_data.get(pid)
            return pd.last_sample_count if pd else 0

    # -- block-I/O flame data ----------------------------------------------

    def blockflame_record_samples(self, pid: int, ts: float, stacks) -> None:
        with self._lock:
            pd = self.blockflame_data.get(pid)
            if pd is None:
                pd = PerfData(pid=pid)
                self.blockflame_data[pid] = pd
            n = 0
            for stack in stacks:
                if not stack:
                    continue
                pd.recent_stacks.append(stack)
                n += 1
            pd.last_sample_ts = ts
            pd.last_sample_count = n

    def blockflame_recent_stacks(self, pid: int):
        with self._lock:
            pd = self.blockflame_data.get(pid)
            if pd is None:
                return []
            return list(pd.recent_stacks)

    def blockflame_last_sample_count(self, pid: int) -> int:
        with self._lock:
            pd = self.blockflame_data.get(pid)
            return pd.last_sample_count if pd else 0

    # -- malloc flame data --------------------------------------------------

    def malloc_record_samples(self, pid: int, ts: float,
                              stacks_with_bytes) -> None:
        """Each item is (stack, bytes_allocated). The flame view weights
        frames by bytes summed, so a code path that allocates ten 1 MB
        buffers shows as ten times the width of one that allocates ten
        100 KB buffers."""
        with self._lock:
            od = self.malloc_data.get(pid)
            if od is None:
                od = OffCpuData(pid=pid)
                self.malloc_data[pid] = od
            total = 0
            for stack, n in stacks_with_bytes:
                if not stack or n <= 0:
                    continue
                od.recent_stacks.append((stack, n))
                total += n
            od.last_sample_ts = ts
            od.last_total_us = total  # repurposed: "total bytes" for malloc

    def malloc_recent_stacks(self, pid: int):
        with self._lock:
            od = self.malloc_data.get(pid)
            if od is None:
                return []
            return list(od.recent_stacks)

    def malloc_last_total_bytes(self, pid: int) -> int:
        with self._lock:
            od = self.malloc_data.get(pid)
            return od.last_total_us if od else 0

    # -- block-I/O latency --------------------------------------------------

    def biolat_record_sample(self, ts: float, p50_us: int, p95_us: int,
                             p99_us: int, total: int) -> None:
        with self._lock:
            self.biolat_samples.append((ts, p50_us, p95_us, p99_us, total))

    def biolat_recent(self) -> List[Tuple[float, int, int, int, int]]:
        with self._lock:
            return list(self.biolat_samples)

    def biolat_p95_series(self) -> List[float]:
        """Just the p95 column, for the Proc panel sparkline."""
        with self._lock:
            return [float(p95) for _, _, p95, _, _ in self.biolat_samples]

    def biolat_iops_series(self, window_seconds: float = 5.0) -> List[float]:
        """Operations-per-second series, derived from the per-window total."""
        if window_seconds <= 0:
            window_seconds = 1.0
        with self._lock:
            return [t / window_seconds
                    for _, _, _, _, t in self.biolat_samples]

    # -- network counters ---------------------------------------------------

    def netstats_record_tcp(self, ts: float, retrans_rate: float,
                            overflow_rate: float, drop_rate: float) -> None:
        with self._lock:
            self.netstats_tcp.append((ts, retrans_rate, overflow_rate, drop_rate))

    def netstats_record_iface(self, ts: float, iface: str,
                              rx_rate: float, tx_rate: float) -> None:
        with self._lock:
            buf = self.netstats_iface.get(iface)
            if buf is None:
                # 180 samples × default 2 s = 6 minutes of NIC history.
                buf = deque(maxlen=180)
                self.netstats_iface[iface] = buf
            buf.append((ts, rx_rate, tx_rate))

    def netstats_tcp_series(self) -> Tuple[List[float], List[float], List[float]]:
        """Returns (retrans_rate, overflow_rate, drop_rate) series."""
        with self._lock:
            r = [s[1] for s in self.netstats_tcp]
            o = [s[2] for s in self.netstats_tcp]
            d = [s[3] for s in self.netstats_tcp]
            return r, o, d

    def netstats_iface_series(self, iface: str) -> Tuple[List[float], List[float]]:
        with self._lock:
            buf = self.netstats_iface.get(iface)
            if buf is None:
                return [], []
            return [s[1] for s in buf], [s[2] for s in buf]

    def netstats_iface_names(self) -> List[str]:
        with self._lock:
            return sorted(self.netstats_iface.keys())

    def netstats_record_states(self, ts: float, counts: Dict[str, int],
                               listen: List[Tuple[int, int]]) -> None:
        with self._lock:
            self.netstats_states.append((ts, dict(counts), list(listen)))

    def netstats_states_latest(self) -> Tuple[Dict[str, int],
                                              List[Tuple[int, int]]]:
        """({state: count}, [(port, recv_q), …]) for the most recent
        snapshot, or ({}, []) if no sample has been taken yet."""
        with self._lock:
            if not self.netstats_states:
                return {}, []
            _, c, l = self.netstats_states[-1]
            return dict(c), list(l)

    def netstats_state_series(self, state_name: str) -> List[float]:
        """History of count-over-time for one connection state, for
        the Network panel's per-state trend sparklines."""
        with self._lock:
            return [float(s[1].get(state_name, 0))
                    for s in self.netstats_states]

    # -- run-queue latency --------------------------------------------------

    def runqlat_record_sample(self, ts: float, p50_us: int, p95_us: int,
                              p99_us: int, total: int) -> None:
        with self._lock:
            self.runqlat_samples.append((ts, p50_us, p95_us, p99_us, total))

    def runqlat_p95_series(self) -> List[float]:
        with self._lock:
            return [float(p95) for _, _, p95, _, _ in self.runqlat_samples]

    # -- perf stat (PMU counters) ------------------------------------------

    def perfstat_record_sample(self, ts: float, pid: int, ipc: float,
                               cache_miss_rate: float,
                               branch_miss_rate: float,
                               counters: Dict[str, int]) -> None:
        with self._lock:
            self.perfstat_samples.append(
                (ts, pid, ipc, cache_miss_rate, branch_miss_rate)
            )
            self.perfstat_counters = dict(counters)

    def perfstat_ipc_series(self) -> List[float]:
        with self._lock:
            return [s[2] for s in self.perfstat_samples]

    def perfstat_cache_miss_series(self) -> List[float]:
        with self._lock:
            return [s[3] for s in self.perfstat_samples]

    # -- alerts (cross-panel) ----------------------------------------------

    def upsert_alert(self, alert: Alert) -> None:
        """Insert a new alert or refresh an existing one with the same id.

        Re-firing detectors call this every cycle while their
        condition holds. The dismissed flag is preserved on update so
        the operator's "I saw it" choice sticks for the lifetime of
        the alert. raised_ts is preserved on update; last_seen_ts is
        always advanced.
        """
        with self._lock:
            now = time.time()
            existing = self.alerts.get(alert.id)
            if existing is None:
                if alert.raised_ts == 0.0:
                    alert.raised_ts = now
                alert.last_seen_ts = now
                self.alerts[alert.id] = alert
                return
            existing.last_seen_ts = now
            # Refresh the human-readable fields in case the title or
            # detail carries live numbers (e.g. "{rate} faults/s").
            existing.title = alert.title
            existing.detail = alert.detail
            existing.severity = alert.severity
            existing.suggested_panel = alert.suggested_panel
            existing.suggested_action = alert.suggested_action
            existing.docs_anchor = alert.docs_anchor

    def gc_alerts(self, now: Optional[float] = None) -> int:
        """Drop alerts whose detector has not refired for STALE_AFTER_SECONDS.

        Returns the number of alerts dropped. Called from the redraw
        loop so stale alerts disappear from the UI naturally without a
        dedicated background task.
        """
        if now is None:
            now = time.time()
        with self._lock:
            stale = [aid for aid, a in self.alerts.items()
                     if (now - a.last_seen_ts) > STALE_AFTER_SECONDS]
            for aid in stale:
                del self.alerts[aid]
            return len(stale)

    def alert_dismiss(self, alert_id: str) -> None:
        with self._lock:
            a = self.alerts.get(alert_id)
            if a is not None:
                a.dismissed = True

    def alerts_active(self) -> List[Alert]:
        """All alerts not yet dismissed, sorted by severity desc then age desc."""
        with self._lock:
            active = [a for a in self.alerts.values() if not a.dismissed]
            active.sort(
                key=lambda a: (-a.severity_rank(), -a.raised_ts)
            )
            return active

    def alerts_for(self, panel_letter: str) -> List[Alert]:
        """Active alerts whose `suggested_panel` matches this panel's
        mnemonic. Used by the panel-banner helper on each redraw."""
        with self._lock:
            return [a for a in self.alerts.values()
                    if not a.dismissed and a.suggested_panel == panel_letter]

    def alerts_summary(self) -> Tuple[int, str]:
        """(count, highest_severity) for the tab-bar badge.
        highest_severity is '' when count is 0."""
        with self._lock:
            active = [a for a in self.alerts.values() if not a.dismissed]
            if not active:
                return 0, ""
            top = max(active, key=lambda a: a.severity_rank())
            return len(active), top.severity

    def alerts_unviewed(self) -> List[Alert]:
        """Alerts the operator has not yet acknowledged. The global
        notification strip at the top of the screen reads from this
        list; it disappears the moment the list is empty."""
        with self._lock:
            return [a for a in self.alerts.values()
                    if not a.dismissed and not a.viewed]

    def mark_alerts_viewed(self) -> None:
        """Flip every active alert's `viewed` flag. Called by the app
        when the operator opens the `!` overlay (acknowledgement of
        existence) or dismisses the global strip with Esc."""
        with self._lock:
            for a in self.alerts.values():
                if not a.dismissed:
                    a.viewed = True

    # -- vmstats (page cache + reclaim) ------------------------------------

    def vmstats_record(self, ts: float, majflt_rate: float,
                       kswapd_rate: float, direct_rate: float,
                       scan_rate: float, cache_kb: int,
                       mem_total_kb: int, mem_avail_kb: int) -> None:
        with self._lock:
            self.vmstats_samples.append((
                ts, majflt_rate, kswapd_rate, direct_rate, scan_rate,
                cache_kb, mem_total_kb, mem_avail_kb,
            ))

    def vmstats_direct_series(self) -> List[float]:
        """Direct-reclaim pages-per-second series for the sparkline."""
        with self._lock:
            return [s[3] for s in self.vmstats_samples]

    def vmstats_majflt_series(self) -> List[float]:
        """Host-wide major-fault rate series — different from the per-PID
        majflt graph: this includes faults from any process on the host,
        which is the right reading when something other than smartmetd
        is reading from disk."""
        with self._lock:
            return [s[1] for s in self.vmstats_samples]
