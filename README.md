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

The RPM package name is `smartmet-monitor`. It requires `python3 >= 3.9`.

## `bstat` family — offline log analysis

Each tool accepts a log file path (or reads stdin) and writes a
Unicode-block summary to the terminal.

```sh
bstat    [-i 1s|10s|1m|10m|1h|1d] [-w WIDTH] [--ascii] [LOG]
bchart   [-i INTERVAL] [-m reqs|ms|kb|mb|err] [LOG]
burls    [-n N] [-s reqs|ms|kb|mb] [LOG]
bstatus                                   [LOG]
bkeys    [-n N] [-s reqs|ms|mb]           [LOG]
```

Default log path: anything matching `/var/log/smartmet/*-access-log` if
you omit the argument on a SmartMet host.

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
`--ascii` / `-w` / a log file path. New scripts should prefer the
`bstat -i X` form directly.

### `--ascii` mode

Pass `--ascii` to any `bstat*` invocation to render bars with `=`
instead of Unicode eighth-blocks, and skip the sparkline footer.
Useful for scripts that grep the output, or for terminals without
reliable UTF-8 support.

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
smtop [-l PATH-OR-GLOB ...] [-u LABEL=URL,URL ...] [-n SECONDS] [--replay]
```

Panels (switch with `1..7` or `Tab`/`Shift-Tab`):

1. **Overview** — totals (1m/5m/60m) plus four btop-style mini-charts
   (requests/min, mean ms, MB/min, error %) and a full-width
   request-rate sparkline.
2. **URLs** — live, sortable table with p50/p95/max latency, mean size,
   error %, and a per-URL latency sparkline. Press Enter to drill into
   a URL: windowed stats, 60-minute mean-latency sparkline, exponential
   histogram, status-code breakdown, and top API keys using that URL.
   `j/k/n/p` walk through URLs without leaving the drill-in.
3. **Caches** — per-cache size / hit rate / hits-per-minute bars plus
   a trend sparkline (from polled history).
4. **Services** — per-handler request rate + trend sparkline.
5. **Active** — in-flight requests sorted by descending duration.
6. **Logs** — raw access-log tail with `/` filter.
7. **Keys** — per-API-key aggregate stats; Enter drills into the key
   to see top URLs it calls.

### Data sources

* **Log tail** — pass one or more `-l PATH` (or glob). Multiple log
  files are tailed concurrently; rotation is detected via inode change.
* **Admin plugin** — pass `-u http://host:8080/admin`. Multiple hosts
  may be configured with repeated `-u` or comma-separated values, and
  each URL can be given a label: `-u prod=http://a/admin,dev=http://b/admin`.
  The panel chrome shows per-host status. Role (frontend/backend/mixed)
  is auto-detected from `?what=list` on startup.

### Key reference (excerpt)

| Key              | Effect                                              |
|------------------|-----------------------------------------------------|
| `1` – `7`        | jump to panel by number                             |
| `Tab` / `Shift-Tab` | next / previous panel                            |
| `?` / `F1`       | help overlay                                        |
| `↑↓` `jk` `PgUp` `PgDn` `gG` | cursor and page movement                |
| `Enter`          | drill into selected URL / API key                   |
| `j/k/n/p`        | next / prev entry inside a drill-in                 |
| `/`              | filter (URLs / Keys / Logs)                         |
| `s` / `S`        | cycle sort column forward / back                    |
| `r`              | reverse sort                                        |
| `[` / `]`        | shrink / grow time window (1 / 5 / 15 / 60 min)     |
| `H` / `T` / `K`  | toggle histogram / status / API-key sections in URLs drill-in |
| `e` / `E`        | export current panel as CSV / JSON                  |
| `q` / `Ctrl-C`   | quit                                                |

Exports are written to `$SMARTMET_MONITOR_EXPORT_DIR`
(falls back to `$SMARTMET_TOP_EXPORT_DIR`, then `/tmp`). A toast
reports the exact path after write.

### Memory model

* Per-URL stats are kept as one exponential-bin histogram (40 bins,
  base 1.5) per minute, retained for 60 minutes. ~20 KB per URL.
* Admin-plugin snapshots retain 300 samples per entity per host
  (≈ 10 minutes at the default 2-second poll cadence).

## Building the RPM

```sh
git clean -fdx rpmbuild
make rpm
```

The resulting `smartmet-monitor-<version>-<release>.noarch.rpm` installs
everything under `/usr/bin`, `/usr/share/smartmet`, and the distribution
site-packages directory (e.g. `/usr/lib/python3.9/site-packages/smartmet_top`).
