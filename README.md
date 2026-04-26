# smartmet-monitor

Log analysis and live monitoring tools for SmartMet Server. Ships two
complementary command-line programs:

| Command                          | What it does                                                         |
|----------------------------------|----------------------------------------------------------------------|
| `bstat`, `bchart`, `burls`, `bstatus`, `bkeys` | Offline analysis of access-log files (Bash + gawk). |
| `smtop`                          | Interactive curses dashboard that tails logs and polls `/admin`.     |

Both parts are implemented against the Python 3 and GNU Awk 5 standard
libraries. No third-party runtime dependencies are required.

## Installation

```sh
make install                  # installs under /usr/{bin,share,lib/pythonX}/
make rpm                      # builds an RPM under ./rpmbuild/RPMS/noarch/
```

The RPM package name is `smartmet-monitor`. It requires Python 3.9
(the `python3` package on RHEL 10 / Fedora, or the `python39`
AppStream module on RHEL 8) plus `gawk`.

On a fresh builder, install the build dependencies straight from the
spec before running `make rpm`:

```sh
sudo yum-builddep smartmet-monitor.spec     # RHEL 8
sudo dnf builddep smartmet-monitor.spec     # RHEL 10 / Fedora
```

## `bstat` family — offline log analysis

Each tool accepts a log file path (or reads stdin) and writes a
Unicode-block summary to the terminal. Click a command name to jump
to its section:

| Command | Synopsis |
|---------|----------|
| [`bstat`](#bstat--bucketed-dashboard)                              | `[-i INTERVAL] [-w WIDTH] [-h HEIGHT] [--ascii] [LOG]` |
| [`bchart`](#bchart--single-metric-vertical-chart)                  | `[-i INTERVAL] [-m reqs\|ms\|kb\|mb\|err] [-h HEIGHT] [-w CELLW] [--ascii] [LOG]` |
| [`burls`](#burls--top-urls-with-query-string-filtering)            | `[-n N] [-s reqs\|ms\|kb\|mb] [-d\|-k LIST] [-L\|-i] [LOG]` |
| [`bstatus`](#bstatus--http-status-code-distribution)               | `[-i INTERVAL] [-h HEIGHT] [--ascii] [LOG]` |
| [`bkeys`](#bkeys--top-api-keys)                                    | `[-n N] [-s reqs\|ms\|mb] [LOG]` |

`INTERVAL` is one of `1s | 10s | 1m | 2m | 5m | 10m | 1h | 1d`.
Most intervals snap to a digit boundary, so they are extracted by
truncating the ISO-8601 timestamp; `2m` and `5m` use minute-rounding
instead. `-h HEIGHT` sets the Braille chart height (in character rows)
consistently for `bstat`, `bchart`, and `bstatus` — defaults to `4`
for the sparkline footer in bstat / bstatus and `12` for bchart.

Default log path: anything matching `/var/log/smartmet/*-access-log` if
you omit the argument on a SmartMet host.

### `bstat` — bucketed dashboard

```sh
bstat -i 1h          wms-access-log         # default sparkline height (4)
bstat -i 1h -h 1     wms-access-log         # compact, single-row sparklines
bstat -i 1h -h 5     wms-access-log         # taller multi-row sparklines
```

Per-row half-height bars for requests / latency / size / bandwidth,
with multi-row Braille sparklines underneath that show each metric
as a time series. `-h` tunes the sparkline height: `-h 1` collapses
to a single dot-ramp row that fits short terminals, while higher
values give 4 dot rows of vertical resolution per char-row.

Compact form (`-h 1`):

![bstat -i 1h -h 1: compact single-row sparkline mode on a 24-hour WMS log](doc/images/bstat_1h_H1.png)

Taller form (`-h 5`) on the same data — the sparkline shape becomes
much easier to read:

![bstat -h 5: taller multi-row Braille sparklines for screens with vertical room](doc/images/bstat_H5.png)

### `bchart` — single-metric vertical chart

```sh
bchart -i 10m -m reqs           wms-access-log     # default chart height (12)
bchart -i 10m -m reqs -h 16     wms-access-log     # taller chart
bchart -i 10m -m ms             timeseries-access-log
```

Braille vertical chart, two buckets per character cell, levels 0-4
per char-row. `-h HEIGHT` sets the chart height in character rows
(default `12`); vertical resolution is `HEIGHT × 4` dots in Braille
mode. Latency varies more than request count and shows the
encoding's vertical resolution:

![bchart -m reqs: requests-per-bucket bar chart](doc/images/bchart_requests.png)
![bchart -m ms: mean-latency bar chart over the same window](doc/images/bchart_latency.png)

### `burls` — top URLs with query-string filtering

```sh
burls -L wms-access-log                       # discover query params
burls    wms-access-log                       # full URL grouping
burls -d bbox,time wms-access-log             # collapse noisy params
burls -i wms-access-log                       # interactive: list, prompt, run
```

Per-service access logs share a path prefix (`/wms`, `/timeseries`
…), so the query string is what distinguishes traffic. By default
`burls` groups on the full URL — different parameter sets become
separate rows. `-L` prints a frequency table of every distinct
parameter name in the log; pick noisy ones (typically `bbox`,
`time`, `latlon`) and pass them via `-d` to collapse otherwise
identical entries. `-i` runs `-L`, prompts for a comma-separated
drop-list on stdin, and re-runs the analysis with that filter.

![burls -L: query-string parameter frequency table](doc/images/burls_list.png)
![burls (full-URL grouping): GetMap variants vs GetCapabilities visible as separate rows](doc/images/burls_full.png)

### `bstatus` — HTTP status code distribution

```sh
bstatus -i 1h         wms-access-log     # default sparkline height (4)
bstatus -i 1h -h 6    wms-access-log     # taller per-class sparkline
```

Aggregate code distribution + per-class breakdown. With `-i`,
prepends a per-class Braille sparkline showing how each class
(2xx / 3xx / 4xx / 5xx) moved over time. `-h HEIGHT` controls the
per-class sparkline height in character rows (default `4`, same
as bstat's footer):

![bstatus -i 1h: per-class Braille sparkline + aggregate distribution](doc/images/bstatus.png)

### `bkeys` — top API keys

```sh
bkeys -n 20    wms-access-log              # top 20 by request count
bkeys -n 20 -s ms  wms-access-log          # top 20 by total time spent
bkeys -n 20 -s mb  wms-access-log          # top 20 by bandwidth
```

Per-API-key aggregate stats (request count, mean latency, total
megabytes) with a horizontal half-height bar scaled to the top
key. Sort key chosen with `-s`.

### Legacy compatibility aliases

The package also installs six compatibility commands that pin the
bucket size. These exist so operator muscle memory and older scripts
continue to work during a gradual rollout:

| Alias       | Equivalent        |
|-------------|-------------------|
| `bstat1s`   | `bstat -i 1s`     |
| `bstat10s`  | `bstat -i 10s`    |
| `bstat1`    | `bstat -i 1m`     |
| `bstat10`   | `bstat -i 10m`    |
| `bstat60`   | `bstat -i 1h`     |
| `bstat24`   | `bstat -i 1d`     |

All six forward their arguments to `bstat`, so you can still pass
`--ascii` / `-w` / `-h` / a log file path. New scripts should prefer
the `bstat -i X` form directly.

### `--ascii` mode

Pass `--ascii` to `bstat`, `bchart`, or `bstatus` to render with
plain ASCII (`=` bars, `. : | #` sparkline ramp) instead of
half-height Unicode and Braille. The sparkline footer collapses to
a single dot-ramp row regardless of `-h`. Useful for scripts that
grep the output, or for terminals without reliable UTF-8 support.

![bstat --ascii: pure-ASCII layout for grep-friendly output](doc/images/bstat_ascii.png)

The tools parse the SmartMet access-log format produced by
`spine/AccessLogger.cpp`:

```
IP - - [END_TIME] "METHOD URL HTTP/VER" STATUS [START_TIME] DUR_MS BYTES "ETAG" APIKEY
```

All five commands share `/usr/share/smartmet/bstat.sh` as a library; set
`SMARTMET_MONITOR_LIB=/path/to/bstat.sh` to point at a different one
(used when running from a source checkout).

## `smtop` — live dashboard

```sh
smtop [-l PATH-OR-GLOB ...] [-u LABEL=URL,URL ...] [-n SECONDS] \
      [--replay] [--replay-bytes N] [--include-rotated] \
      [--history-minutes N] [--ascii] [--perf] [--perf-interval SEC]
```

A typical production invocation, used for every screenshot below:

```sh
smtop --perf --replay -u http://127.0.0.1:8081/admin
```

`--perf` enables the live flamegraph and perf-top symbol view (requires
`perf` from `linux-tools` plus root or `kernel.perf_event_paranoid <= 2`),
`--replay` populates the panels from the tail of every
`/var/log/smartmet/*-access-log` on startup, and `-u` points at the
SmartMet admin plugin for the polled cache/service/active-request data.

![smtop default startup view (Live composite) on a SmartMet backend](doc/images/monitor_top.png)

Each panel has one **red highlighted letter** in its tab label — pressing
that letter (case-insensitive) jumps directly to it; `Tab`/`Shift-Tab`
cycle through panels. Sparklines and charts use Braille (U+2800..U+28FF)
for 2× horizontal density and 4× vertical resolution; pass `--ascii` to
fall back to eighth-block characters on terminals that don't render
Braille well.

**Composite views** (multiple panels visible at once — the long-term
direction; the dedicated single-panel views below remain for sortable
/ filterable interaction):

1. l**i**ve — Graphs panel (per-plugin live, top 60%) + URLs panel
   (bottom 40%). The default startup view when log files are
   configured. Operator goal: "which plugin is busy and which URLs
   inside it are slow?"

   ![Live composite: per-plugin Graphs on top, sortable URLs table underneath](doc/images/monitor_live.png)

2. **h**ealth — Caches (top), Services (middle), Active in-flight
   (bottom), in equal thirds. Operator goal: "is this server healthy?"
3. **f**lame — full-screen live flamegraph for the focused
   `smartmetd` PID. Requires `--perf`. Rebuilds every perf cycle from
   the entire retained stack ring (~20 000 samples). Cursor keys
   navigate the tree, Enter zooms into the selected frame, Esc / `u`
   zooms out, `0` / Home resets to the root. `s` opens a preset
   menu (1 / 3 / 5 / 10 / 20 / 30 s) for the per-cycle record
   duration; the new value takes effect on the next cycle without
   restarting smtop. The lower portion of the screen carries the
   perf-top symbol list so nothing is wasted on shallow stacks.

   Press **`o`** to toggle between the on-CPU flamegraph (default)
   and a parallel **off-CPU** flamegraph that shows where threads
   are blocked: futex / mutex waits, I/O, sleeps. Off-CPU stacks
   come from `bcc-tools`' `offcputime-bpfcc -p PID -f SECS`,
   weighted by microseconds-blocked per stack. The Flame view
   surfaces an install hint inline when `bcc-tools` is missing
   (`sudo dnf install bcc-tools` on RHEL 8 / Fedora). The "Top
   blocked-on functions" list at the bottom of the panel
   replaces the on-CPU top-symbols list when in this mode. This
   is the canonical answer to "the request is slow but on-CPU
   shows nothing".

   ![Flame view: live flamegraph for smartmetd plus perf-top symbol list](doc/images/monitor_flame.png)

**Single-panel views**:

4. **O**verview — totals (1m/5m/60m) plus four mini-charts
   (requests/min, mean ms, MB/min, error %) and a full-width
   request-rate sparkline.
5. **G**raphs — live per-plugin access-log monitor. One row per
   `*-access-log` file with req/s, mean/p95 latency, error %, and two
   independently auto-scaling Braille sparklines (response time +
   response size) over the last 60 seconds at 1-second resolution.
   `m` toggles time spark mean ↔ p95, `b` toggles size spark
   mean ↔ throughput, `i` shows/hides idle handlers.

   ![Graphs panel: per-plugin tall layout with response-time and response-size charts](doc/images/monitor_graphs.png)
6. **U**RLs — live, sortable table with p50/p95/max latency, mean size,
   error %, and a per-URL latency sparkline. Press Enter to drill into
   a URL: windowed stats, 60-minute mean-latency sparkline, exponential
   histogram, status-code breakdown, and top API keys using that URL.
   ↑/↓ walk through URLs without leaving the drill-in.

   ![URLs panel: per-endpoint latency table; note /download's chunked-transfer-inflated total bytes](doc/images/monitor_urls.png)

7. **C**aches — per-cache size / hit rate / hits-per-minute bars plus
   a trend sparkline (from polled history).

   ![Caches panel: per-cache hit rate, size and trend](doc/images/monitor_caches.png)

8. **S**ervices — per-handler request rate + trend sparkline.

   ![Services panel: per-handler request rates and tall trend charts](doc/images/monitor_services.png)

9. **A**ctive — in-flight requests sorted by descending duration. The
   Braille sparkline at the top tracks the in-flight count over the
   recent admin-poll history.

   ![Active panel: in-flight count sparkline at the top, current requests below](doc/images/monitor_active.png)

10. **P**roc — `/proc`-based memory + I/O for each `smartmetd` process
   on the host, with RSS-split sparklines (file-backed vs anon vs
   shmem), `VmPTE`, swap, FDs, and on-demand `smaps_rollup`. Multiple
   smartmetd PIDs (frontend + backend) are switched via `n`/`N`. With
   `--perf`, the panel adds a live perf-top symbol view and a Braille
   flamegraph that updates each cycle (`f` toggles between them).

   ![Proc panel: memory + I/O + perf-top symbols for the focused smartmetd PID](doc/images/monitor_proc.png)

11. **L**ogs — multi-source `tail -F`. Each tailed plugin has its own
    ring buffer; the panel shows a tab bar of plugin names with the
    focused one marked, and ←↑→↓ switch between them. There's also
    an `[all]` virtual entry that pulls from a merged ring across
    every plugin. Enter / End jumps to the live tail; `/` filters
    within the focused source.

    ![Logs panel: per-source tab bar with the focused log's tail bottom-anchored below](doc/images/monitor_logs.png)

12. Api**k**eys — per-API-key aggregate stats; Enter drills into the
    key to see top URLs it calls.

### Data sources

* **Log tail** — pass one or more `-l PATH` (or glob). Multiple log
  files are tailed concurrently; rotation is detected via inode change.
* **Admin plugin** — pass `-u http://host:8080/admin`. Multiple hosts
  may be configured with repeated `-u` or comma-separated values, and
  each URL can be given a label: `-u prod=http://a/admin,dev=http://b/admin`.
  The panel chrome shows per-host status. Role (frontend/backend/mixed)
  is auto-detected from `?what=list` on startup.

  Smoke-test the admin URL with `wget` or `curl` before pointing
  `smtop` at it — the `?format=json` endpoints are what `smtop`
  polls:

  ![Verifying the admin endpoint with wget against a SmartMet backend](doc/images/monitor_wget.png)

### Key reference (excerpt)

| Key              | Effect                                              |
|------------------|-----------------------------------------------------|
| `i h f o g u c s a p l k` | jump to view / panel by mnemonic letter (highlighted red in tab) |
| `Tab` / `Shift-Tab` | next / previous panel                            |
| `?` / `F1`       | help overlay                                        |
| `↑` `↓` `←` `→` `PgUp` `PgDn` `Home` `End` | cursor and page movement |
| `Enter`          | drill into selected URL / API key                   |
| `↑` / `↓`        | next / prev entry inside a drill-in                 |
| `/`              | filter (URLs / Keys / Logs)                         |
| `s` / `S`        | cycle sort column forward / back (URLs/Keys panels) |
| `r`              | reverse sort, or run `smaps_rollup` (Proc panel)    |
| `[` / `]`        | shrink / grow time window (1 / 5 / 15 / 60 min)     |
| `n` / `N`        | next / prev smartmetd PID (Proc and Flame panels)   |
| `1` – `9`        | select smartmetd PID by index in the selector at the top of Proc / Flame |
| `f`              | toggle inline flamegraph (Proc); also the Flame view mnemonic |
| `↑↓←→` `Enter` `Esc/u` `0` | navigate / zoom in / zoom out / reset (Flame view) |
| `m` / `b` / `i`  | toggle time spark / size spark / idle handlers (Graphs panel) |
| `e` / `E`        | export current panel as CSV / JSON                  |
| `q` / `Ctrl-C`   | quit                                                |

Exports are written to `$SMARTMET_MONITOR_EXPORT_DIR`
(falls back to `$SMARTMET_TOP_EXPORT_DIR`, then `/tmp`). A toast
reports the exact path after write.

### Reading the live monitors

Every panel that draws a sparkline or histogram answers a specific
operational question. The intent here is the
[Brendan Gregg](https://www.brendangregg.com/) school of
performance analysis: each metric is part of the
[USE method](https://www.brendangregg.com/usemethod.html) story
(Utilization / Saturation / Errors), and most have a "shape that
means trouble" you can pattern-match on at a glance.

#### Major page faults (Proc panel)

**What it measures.** Page faults per second that required a
synchronous read from disk — the kernel's "I asked for a page
that wasn't resident in RAM" counter, taken from
`/proc/PID/stat` field 12 (`majflt`) and rate-converted across
samples. Saturation metric in USE-method terms.

**Detects.** Working-set eviction (the QueryData files
just-published by a fresh model run no longer fit in page
cache); host-wide memory pressure stealing pages from
smartmetd; an mmap-and-discard access pattern hidden inside a
plugin; the moment a SmartMet server stops being CPU-bound and
starts being disk-bound.

**Likely causes when it goes red.**
- A producer just published new files large enough to push the
  hot working set out of cache.
- Another process on the same host (a backup, a scheduled
  conversion, a colleague's experiment) suddenly demanded
  several GB of RAM.
- `vm.vfs_cache_pressure` was nudged up, or
  `vm.drop_caches` was written, evicting cached pages.
- A plugin is touching previously-unread files (e.g. broad
  `param=` or `producer=` enumeration in WMS / timeseries).

**Healthy shape.** Flat at 0 / s, with a thin floor of ones and
twos on a steady-state server.

**Trouble shape.** Bursts of hundreds-to-thousands per second
shortly after a model-run timestamp. On-CPU flame stays
innocuous, the access log shows no errors, CPU utilisation looks
idle — and request p95 jumps several fold for the spike's
duration. A *sustained* > 50 / s for minutes means the working
set has permanently outgrown RAM.

**What to look at next.** Pair this with `Block I/O latency
(host)` underneath: if both spike together, the disk absorbed
the fault traffic; if faults spike but I/O p95 stays flat, the
storage is keeping up and the latency is purely the time spent
reading. `RssFile` in the Memory section falling at the same
moment confirms cache eviction.

#### Block I/O latency (Proc panel)

**What it measures.** Power-of-2 histogram of every block-device
operation completed during a 5-second window, summarised as
p50 / p95 / p99 microseconds plus the IOPS count. Sourced from
`biolatency-bpfcc` which probes the kernel block layer. Latency
metric in USE-method terms; pair with the IOPS to read
saturation as well.

**Detects.** Slow storage (failing disk, contended SAN volume,
noisy-neighbour VM); a sudden change in workload mix (lots of
small reads vs few large reads); fsync storms from a misconfigured
log writer; the moment the disk starts to be the bottleneck.

**Likely causes when it goes red.**
- A model-run publish that drives a wave of major faults (the
  page-fault graph above will spike at the same moment).
- A backup or `dd` running on the same volume.
- An LVM snapshot or filesystem resize in progress.
- The underlying SSD reaching its endurance / queue limit
  (sustained high p99 with flat IOPS = the device is the
  bottleneck, not the workload).

**Healthy shape.** p50 in the low microseconds (page-cache hits),
p95 a few hundred microseconds, p99 ≤ a few milliseconds, all
flat over time.

**Trouble shape.** p95 steady over several windows in the
multi-millisecond range — typical of saturated rotational disk
or a network-attached volume that has lost its cached layer.
A *spike* with IOPS climbing in tandem is just "you got busier";
a *spike* with IOPS flat or falling is "the disk got slower".

**What to look at next.** Major page faults above (suggests
mmapped working-set churn driving the I/O); `iostat -x 5` on the
host for queue-depth detail; `dmesg` for filesystem / driver
errors; the access log for any handler that newly correlates
with the latency spike (a plugin that pulled a cold dataset).

#### Run-queue latency (Proc panel)

**What it measures.** Power-of-2 histogram of the time each
thread spent ready-but-not-running over a 5-second window,
summarised as p50 / p95 / p99 microseconds plus the total
context-switch count. Sourced from `runqlat-bpfcc`, which
instruments `sched:sched_wakeup` and `sched:sched_switch`.
Saturation metric for the CPU resource.

**Detects.** Scheduler-side latency that CPU utilisation alone
cannot show. The textbook case: CPU looks idle, threads still
take milliseconds to run, requests pile up. Most useful on
virtualised hosts; on dedicated bare metal it should sit near
zero unless the host is genuinely overloaded.

**Likely causes when it goes red.**
- *Container CFS throttling.* The cgroup hit its `cpu.cfs_quota`
  ceiling and the kernel parked all its threads. Cross-check
  `/sys/fs/cgroup/.../cpu.stat` for `nr_throttled` /
  `throttled_time`.
- *Noisy neighbour VM.* Another guest on the hypervisor is
  pinning the same cores. If your VM's `steal time`
  (`vmstat 1`, the `st` column) climbs at the same moment, this
  is the cause.
- *Too many runnable threads for too few CPUs.* Classic
  saturation. The Active panel shows in-flight count climbing
  in tandem.
- *Real-time tasks pre-empting smartmetd* (rare on production
  servers — `chrt -p PID` shows the scheduling class).

**Healthy shape.** On bare metal: p50 in single-digit
microseconds, p95 < 100 µs, p99 < 1 ms, all flat. On a
healthy VM: same shape, with occasional p99 blips into the
low-millisecond range when the hypervisor briefly stalls.

**Trouble shape.** p95 sustained over 1 ms (red in the panel)
means the scheduler is stealing real time from your work. p99
stuck in the tens-of-milliseconds range during peak hours is
the smoking gun for a CFS quota that's too tight.

**What to look at next.** `vmstat 1`'s `st` (steal) column for
hypervisor pre-emption; `cat /sys/fs/cgroup/$UNIT/cpu.stat` for
throttled time; the on-CPU flame to confirm work *is* being
scheduled in pipe bursts rather than continuously; the URLs
panel for which handlers' p95 follows runqlat's shape — those
are the operations actually being held off CPU.

#### CPU efficiency — IPC + cache & branch miss rates (Proc panel)

**What it measures.** Three derived ratios from a short
`perf stat -e cycles,instructions,cache-references,
cache-misses,branch-misses -p PID -- sleep N`:

  - **IPC** = instructions per cycle. The single best one-number
    summary of "is this CPU work efficient?".
  - **Cache miss rate** = cache-misses / cache-references.
    Where the LLC fits the working set.
  - **Branch miss rate** = branch-misses / instructions.
    How predictable the hot loop's control flow is.

**Detects.** Memory-bound code (low IPC + high cache-miss); a
working set that just outgrew the L3 cache (cache-miss rate
suddenly steps up); badly-vectorised hot paths; an architecture
mismatch with the binary (branch-miss rate elevated permanently);
the moment a code change ships and the IPC drops without any
other metric reacting.

**Likely causes when it goes red.**
- *IPC < 0.3 sustained:* the CPU is stalling, almost always on
  memory. Look at the on-CPU flame for which function is hot;
  if it's a tight loop over `std::map<>`, `std::unordered_map`
  with a poor hash, or a virtual-call-heavy traversal, that's
  the culprit. Cross-reference page-fault rate (cold pages) and
  cache-miss rate (warm but evicted-from-LLC).
- *Cache miss > 30%:* the working-set-per-core is bigger than
  the L3 cache slice. Either trim the data (smaller queries,
  better filtering) or pin SmartMet to fewer cores so each gets
  more cache.
- *Branch miss > 5%:* unpredictable branches. Most of the time
  this is a hot data-dependent `if`; sometimes it's a binary
  built with an old `-march=` that disables modern branch
  predictor hints.
- *All three OK but URLs are slow anyway:* the bottleneck is
  off-CPU. The off-CPU flame is your next stop.

**Healthy shape.** SmartMet workloads typically run between IPC
0.5 and 1.0 with cache-miss rate 5–15% and branch-miss rate
under 2%. Steady traces, both numbers tracking traffic levels.

**Trouble shape.** A sustained IPC dip on a single PID while
others stay healthy points at one plugin or one connection's
hot path. A *step change* in cache-miss rate at a deploy
boundary is the canonical "the new build has worse memory
locality" signal. Cache-miss flapping between low and high
across cycles often means the host is shared and the cache is
being trampled by a neighbour.

**What to look at next.** The on-CPU flame for the function
where time is being spent; the off-CPU flame to confirm the
work is actually CPU-bound rather than blocking; runqlat to
rule out scheduler-side latency; and the URLs panel to see
which handler's p95 follows the IPC trace — that's the one
the inefficient code is on the hot path of.

#### Network — TCP retransmits + listen drops + NIC bandwidth (Proc panel)

**What it measures.** Three host-wide counters from
`/proc/net/snmp` and `/proc/net/netstat`: TCP retransmitted
segments per second, listen-queue overflows per second, listen
drops per second. Plus per-NIC rx/tx bytes-per-second from
`/proc/net/dev` (loopback omitted). Both saturation (listen
drops) and error (retransmits) signals.

**Detects.** Network saturation between this host and any peer
(retransmits); the application failing to call `accept()` fast
enough (listen drops, listen overflows); a NIC reaching its
bandwidth ceiling; a misbehaving switch / NIC offload bug;
upstream routes that are silently lossy.

**Likely causes when it goes red.**
- *Retransmits > 1 / s sustained:* lossy network path — could
  be a flaky cable / NIC / switch port between this host and a
  client subnet, an overloaded firewall, or the peer's NIC ring
  buffer is full. Compare with the receiving host's stats; if
  both show retrans, the path is the suspect.
- *Listen overflows or drops > 0:* the application is not
  pulling new connections off the accept queue fast enough.
  Either smartmetd is CPU-blocked elsewhere (cross-check
  on-CPU flame), the listen backlog is too small
  (`net.core.somaxconn`, the `listen()` argument), or a
  burst-incoming pattern exceeds it momentarily.
- *NIC rx or tx near interface line-rate:* you have hit the
  pipe's ceiling. A 1 Gbit interface saturates at ~120 MB/s,
  10 Gbit at ~1.2 GB/s. The Active panel will show queries
  piling up because they cannot be drained.

**Healthy shape.** `retrans/s` flat at 0.0 with the occasional
single-segment blip on long-lived TCP connections; both listen
counters at 0; rx/tx tracking actual operational load (peaks
matching expected request volume).

**Trouble shape.** A retransmit floor that never returns to
zero (consistent path loss); spikes of listen drops at request
peaks (accept-queue starvation); rx rate flat-topping with
clear plateaus that match interface line rate (saturated NIC).

**What to look at next.** When retransmits climb,
`ss -s` and `nstat -az TcpRetransSegs TcpExtTCPLostRetransmit`
on this host plus the same on a peer — if only one side reports,
it's local; both sides, it's the path. When listen drops appear,
look at the on-CPU flame for stalls in the accept loop, then
`ss -lnt` for current backlog vs `Send-Q` ceiling. When NIC
saturates, the URL panel ranks the bandwidth-heaviest endpoints
that you may want to throttle or cache.

### Cross-panel alerts

smtop runs a small set of detectors against each metric source. When
a detector's threshold is met, an `Alert` is raised into a central
list that every panel reads on redraw. The result is a single
operator-facing surface for "something is wrong somewhere", visible
no matter which panel you happen to be on:

- **Tab-bar badge**, top right of every screen. `⚠ N alert(s)`,
  coloured by the highest severity present (red = `crit`, yellow =
  `warn`, dim = `info`). Always visible.
- **Global notification strip**, drawn in **bold blinking red /
  yellow** above the active panel as soon as a *new* alert is
  raised. Disappears the moment the operator presses `!` (which
  acknowledges every active alert simultaneously). Shows the
  highest-severity title plus a count of additional unviewed
  alerts.
- **Per-panel banner**, drawn just above the panel content when
  an alert names this panel as the place to investigate. Same
  one-row reverse-video bar regardless of which panel is showing
  it — wired through the App, no per-panel code.
- **Modal alerts overlay**, opened with `!` from any panel. Shows
  every active alert with its severity, age, suggested next
  panel, and the full multi-line "Detected / Likely causes /
  What to look at next" body. Keys: `↑/↓` select, `Enter` jump
  to the suggested panel and dismiss the alert in one stroke,
  `d` dismiss without jumping, `Esc` close.

#### Alert lifecycle

1. **Raised** by a detector while its trouble pattern holds —
   e.g. major page-fault rate has been > 100 / s for three
   consecutive samples.
2. **Refreshed** every cycle the condition still holds. The
   alert's `raised_ts` is preserved; only `last_seen_ts`
   advances. Re-firing detectors do not multiply alerts: the
   same `id` is updated in place.
3. **Viewed** when the operator opens the `!` overlay. The
   global strip stops blinking; the alert stays in the badge.
4. **Dismissed** by `Enter` or `d` in the overlay. The alert
   goes silent (badge, banner, overlay all hide it) but the
   detector keeps measuring.
5. **Cleared** automatically when the detector stops re-firing
   for 60 seconds (`STALE_AFTER_SECONDS` in `state/alerts.py`).
   After that the next detector firing creates a fresh,
   non-dismissed alert — so a recurring problem after a quiet
   period re-attracts attention rather than staying silently
   dismissed.

#### Alerts that ship today

| Detector id              | Severity | Suggested panel | What it means |
|--------------------------|----------|-----------------|---------------|
| `majflt-storm`           | warn     | Proc            | Major page faults > 100/s for ≥ 3 consecutive samples on a smartmetd PID. Working set lost from page cache. |
| `biolat-slow`            | warn     | Proc            | Block-device p95 over 10 ms for ≥ 3 consecutive 5 s windows. Storage saturation or major-fault knock-on. |
| `runqlat-stalls`         | warn     | Proc            | Run-queue p95 ≥ 1 ms for ≥ 3 windows. Scheduler-side latency, usually CFS throttling or noisy-neighbour VM. |
| `perfstat-low-ipc`       | warn     | Flame           | IPC < 0.3 for ≥ 2 perf-stat cycles. CPU is stalling on memory or cross-core sync. |
| `netstats-retrans`       | warn     | Proc            | TCP retransmits > 1/s for ≥ 3 cycles. Lossy network path or peer ring-buffer overflow. |
| `netstats-listen-drops`  | crit     | Flame           | Listen-queue overflow / drops at any positive rate. The application is failing to accept connections fast enough. |
| `perf-record-failed`     | warn     | Flame           | perf record returned a non-zero exit. Usually `perf_event_paranoid` or missing `linux-tools`. |

Every `id` above doubles as a README anchor — the overlay lists
the matching `doc/README.md#<id>` link so an operator unsure
about a specific detector's threshold can jump straight to the
"Reading the live monitors" entry that explains it.

### Memory model

* Per-URL stats are kept as one exponential-bin histogram (40 bins,
  base 1.5) per minute, retained for `--history-minutes` (default 60
  minutes). ~20 KB per URL per hour.
* Per-plugin (per-access-log) stats keep 60 1-second buckets plus
  `--history-minutes` 1-minute buckets. ~3 KB per plugin per hour ×
  ~20 plugins ≈ 60 KB per hour. With `--history-minutes 1440` (24 h)
  that's ~17 MB; with `--history-minutes 10080` (7 d) ~120 MB.
* Admin-plugin snapshots retain 300 samples per entity per host
  (≈ 10 minutes at the default 2-second poll cadence).
* Per-PID memory/IO samples retain 1800 ticks (60 min @ 2 s) ≈ 360 KB
  per smartmetd process.

### Replaying historical logs

`--replay` reads the tail of each log file (capped at `--replay-bytes`,
default 1 GB) so the dashboard opens with populated panels rather
than empty ones. Add `--include-rotated` to also read every rotated
sibling (`<base>-YYYYMMDD`, `<base>-YYYYMMDD.gz`) in chronological
order — combined with `--history-minutes 10080` this gives a full
week of context. Compressed `.gz` files are read transparently via
the stdlib `gzip` module; no pip dependency.

## Building the RPM

```sh
make rpm
```

`make rpm` builds a source tarball from `HEAD` and runs `rpmbuild -tb`,
which uses `%_topdir` from `~/.rpmmacros` — the same convention as the
other `smartmet-*` packages in this workspace.

The resulting `smartmet-monitor-<version>-<release>.noarch.rpm` installs
everything under `/usr/bin`, `/usr/share/smartmet`, and the distribution
site-packages directory (e.g. `/usr/lib/python3.9/site-packages/smartmet_top`).
