# Monitoring mmap headroom in SmartMet Server

**Status.** Design analysis only — not implemented. Last revised
2026-05-02. Captured here as a refresher in case the conversation
resumes; not linked from the README.

This file lives outside the install set: it ships in the git
repository but is not packaged into the RPM. The `doc/design-notes/`
directory exists for this kind of forward-looking analysis that
may or may not become work.

## The operator question

SmartMet Server backends routinely keep on the order of two million
file mappings open — GRIB, querydata, NetCDF, DEM, shapefiles. The
default `vm.max_map_count` of 65,530 is far below that, so the FMI
production sysctl override is:

```
# GRIB mapping requires a very large number of files, default is only 65530
vm.max_map_count = 2000000
```

We have no monitor anywhere that follows the per-process mapping
count against this ceiling. An operator question like "how close
are we to the limit on backend N?" has no answer today.

A related but separate developer question: *of the bytes that
smartmetd has mmapped, how many have actually been read into
memory?* — i.e. how much of the address space is real working
set vs unrealised potential demand.

## Why this is hard: the kernel cost constraint

Per-process VMA count is not exposed in any cheap kernel interface
(`/proc/<pid>/status`, `statm`, `stat`). The only authoritative
source is the line count of `/proc/<pid>/maps` — and that read
walks the VMA tree under `mmap_lock`, holding it long enough that
on a 2 M-mapping host the read takes seconds. On RHEL 8 (kernel
4.18) the lock hold blocks every mmap/munmap on the process for
the duration; later kernels with the maple-tree VMA store reduce
but do not eliminate this cost.

`smartmet_top/sources/proc.py` already documents this in its
module docstring:

> per-VMA reads (`maps`, `smaps`) take seconds to complete and hold
> mmap_sem on the target process

**The hard operational ceiling.** A SmartMet backend can serve
upward of 1000 queries per second under storm load. A 2-second
mmap_lock stall therefore queues ~2000 requests behind the read.
That is not acceptable behaviour from a monitoring tool; the
metric would cause the very incident it is meant to predict.

This rules out:

- Any background sampler reading `/proc/<pid>/maps` on any cadence.
- Any opt-in / on-demand read of `/proc/<pid>/maps`, since the
  cost is per read, not per cadence.
- `/proc/<pid>/smaps` and `/proc/<pid>/numa_maps` — same VMA walk.

## Cheap data sources that exist

- `/proc/sys/vm/max_map_count` — the limit. Single int, free.
- `/proc/<pid>/status: VmSize` — total virtual memory mapped, in
  kB. Already polled cheaply. Coarse proxy: includes anon/code/
  stack, not just data files.
- `/proc/<pid>/status: VmRSS` — currently resident bytes. Same
  caveat — coarse proxy.
- eBPF tracepoints / kprobes on the kernel's mmap/munmap path —
  yield deltas only. **No baseline** without a one-time expensive
  read. Useful for leak / churn detection, not for absolute
  headroom.

## The application-level path (recommended if we revisit)

Since the kernel can't give us the count cheaply, but **smartmetd
itself knows exactly how many file mappings it has created** —
because every data-file mmap goes through one of a small set of
C++ wrappers — the right place to track the metric is inside
SmartMet Server, not via /proc.

This mirrors the established `Fmi::Cache` statistics pattern.
Caches expose statistics through `Fmi::Cache::CacheStatistics`,
aggregated across every registered cache via
`spine/Reactor.h:getCacheStats()`, and surfaced through the admin
plugin's `?what=cachestats` endpoint. smtop already polls that
endpoint per host.

### Wrapper inventory

The data-file mappings flow through these wrappers in the
workspace:

| Wrapper | Location | Used by |
|---|---|---|
| `Fmi::MemoryMapper` | `grid-files/src/common/MemoryMapper.cpp` | grid-files / grid-content (GRIB, NetCDF). Uses userfaultfd. |
| `boost::iostreams::mapped_file` | direct, in `newbase/NFmiRawData.cpp` | querydata |
| `boost::iostreams::mapped_file` | direct, in `gis/SrtmTile.cpp` | DEM tiles |

Code mappings (`.so`, the executable text), stack guards and
anonymous allocator pages do **not** pass through these wrappers,
so the registry self-excludes them — exactly what we want.

### Proposed architecture

```cpp
// macgyver/MmapStats.h
namespace Fmi::MmapStats {
  struct Snapshot {
    // Keys are categories ("querydata", "grib1", "grib2", "netcdf",
    // "dem", "shape", ...).
    std::map<std::string, uint64_t> live_count;
    std::map<std::string, uint64_t> live_bytes;
    std::map<std::string, uint64_t> created_total;
    std::map<std::string, uint64_t> created_bytes;
    std::map<std::string, uint64_t> destroyed_total;
    std::map<std::string, uint64_t> destroyed_bytes;
    std::map<std::string, uint64_t> mmap_failures;     // ENOMEM, EACCES, etc.
    // Optional, see "ever-touched" section below.
    std::map<std::string, uint64_t> bytes_first_read;  // uffd-tracked only
  };
  Snapshot capture();   // O(num categories), atomic loads only
}

// macgyver/MappedFile.h — thin RAII wrapper around
// boost::iostreams::mapped_file that bumps the counters in ctor/dtor.
// newbase and gis switch their direct boost users to this.

// spine/Reactor.h
Fmi::MmapStats::Snapshot getMmapStats() const;

// brainstorm/plugins/admin → ?what=mmapstats
```

Atomic counters are free at the rates they update at (file open
/ close events). `capture()` does one atomic load per cell, no
lock. `mmap_failures` is the smoking-gun signal when the process
hits `vm.max_map_count` and starts refusing to map new files —
without it, the failure case is invisible until something else
(query failures, log errors) surfaces it.

### Cross-repo dependency order

Each repo bumps independently per FMI convention.

1. `macgyver` — `Fmi::MmapStats` registry + `Fmi::MappedFile` wrapper.
2. `newbase`, `gis` — switch `boost::iostreams::mapped_file` users
   to `Fmi::MappedFile`.
3. `grid-files` — `MemoryMapper` increments the same registry at
   VMA-create / VMA-destroy points.
4. `grid-content`, querydata-engine, gis-engine — pick up the new
   wrappers transitively.
5. `spine` — `Reactor::getMmapStats()` aggregator.
6. `brainstorm/plugins/admin` — `?what=mmapstats` endpoint.

### smtop integration once the server side lands

Adding to `smartmet_top/sources/adminapi.py`'s per-host poll list,
new `MmapStats` field in `state/store.py`, and one row in the
Proc panel. Cluster Proc panel inherits per-backend mmap data
through the existing fanout. Total smtop work: probably a day,
mostly UI.

## Three quantities, three operational questions

The wrapper-level counters above give us the **virtual mapped
bytes** — sum of `size` passed to `mmap()`. This answers "how
many file mappings does the process hold and how much address
space do they cover", but does not answer "how much RAM do we
actually need".

There are in fact three distinct quantities, all of which mean
"memory used by mmapped files" but answer different operational
questions:

| Metric | What it is | How to obtain |
|---|---|---|
| Virtual mapped bytes | sum of `size` passed to `mmap()` — pure address space | wrapper, free |
| Ever-touched bytes | pages faulted in at least once | uffd handler, free; otherwise expensive |
| Currently resident bytes | pages still in RAM right now (post-reclaim, post-MADV_DONTNEED) | `mincore()` on demand, or `/proc/<pid>/smaps` |

**The grid-files `MemoryMapper` already uses userfaultfd.** Its
fault handler runs on every first read of a page — bumping
`bytes_first_read += page_size` is one atomic add per fault, free
at typical fault rates, and gives a cumulative ever-touched
counter that is real-time accurate for any uffd-managed mapping.
This covers the GRIB / NetCDF stack, which is most of the data
on a SmartMet backend.

**Newbase / gis use plain `boost::iostreams::mapped_file`**, which
has no fault notification. To extend ever-touched coverage to
querydata and DEM we would either:

- Switch them to a uffd-managed wrapper. Substantial refactor; a
  uffd thread per mapping pool.
- Accept partial coverage — virtual mapped bytes only for those
  categories, with a documented gap.

**Currently resident** is a separate question, answered cheapest
by `mincore()` per mapping. Each call holds `mmap_lock` only on
the target VMA, briefly, so individual calls are fine; but
**aggregating across 2 M mappings is 2 M syscalls** which is the
expensive case. The right pattern is an opt-in admin endpoint
(`?what=mmapresidency`) that runs the sweep when an operator
asks, never on a poll cycle. Same gesture as `smaps_rollup`'s
`r` key in the Proc panel today. Cost paid only when the operator
explicitly wants it, during a quiet investigation.

`/proc/<pid>/smaps` would give both ever-touched (`Referenced`)
and resident (`Rss`/`Pss`) per VMA, but it has the same
mmap_lock-stall problem as `/proc/<pid>/maps` and is therefore
ruled out.

## Open questions for if/when we revisit

1. **uffd coverage of newbase / gis.** Worth the refactor for
   uniform ever-touched accounting, or accept partial coverage and
   document the gap?
2. **Granularity of categories.** Seven-ish flat tags
   (`querydata` / `grib1` / `grib2` / `netcdf` / `dem` / `shape` /
   `other`) cover the operational questions cheaply. A path-keyed
   detail endpoint (`?what=mmapstats&detail=paths`) is a separate
   on-demand thing and does not need to live in the per-cycle
   poll.
3. **Per-process mapping count vs `mm->map_count`.** Two opens of
   the same file by different engines may share a single kernel
   VMA (depends on offsets / length); the wrapper sees two
   constructions. Application-level `live_count` will sometimes
   exceed the kernel's `mm->map_count`. The application number is
   "how many file mappings does our code think are open" — usually
   the more useful answer. The kernel ceiling is then the **upper
   bound**, not equal. README would document this.
4. **Snapshot frequency.** Reactor aggregation is cheap, so
   per-second is fine. The question is what smtop does with
   higher-frequency data — a 60-cell sparkline on a 30 s cadence
   covers 30 minutes, which matches typical investigation horizons.
5. **vm.max_map_count itself.** Polled per-host from
   `/proc/sys/vm/max_map_count` (cheap) by smtop's local sampler,
   or echoed from each backend's view (`getrlimit`-equivalent +
   sysctl read inside smartmetd). The latter is more honest about
   what the backend actually sees.

## Why this was deferred

The conversation that produced this analysis happened during
26.5.2-14 (May 2026). Several other features were already in
flight and not yet operator-tested — the cluster Proc panel
(26.5.2-13), the Flame analyse overlay (26.5.2-14) — so
introducing a multi-repo coordinated change touching macgyver,
newbase, gis, grid-files, grid-content, the relevant engines, the
admin plugin, and smtop was rejected as too much in flight at
once. This file is the bookmark.
