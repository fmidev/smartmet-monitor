# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`smartmet-monitor` — two companion operator tools for SmartMet Server, shipped in one RPM:

- **`smtop`** — interactive curses dashboard (Python stdlib, no pip). Tails access logs and polls the SmartMet admin plugin.
- **`bstat` / `bchart` / `burls` / `bstatus` / `bkeys`** — offline access-log analysis commands (Bash + GNU awk). All five are thin wrappers around a single shared library at `share/smartmet/bstat.sh`.

Everything is pure stdlib — **no pip or third-party packages are used at runtime, and this is deliberate**. If a feature needs a new import, first check whether the Python 3.9 stdlib covers it. Adding a runtime dependency is a design decision, not a mechanical one.

## Common commands

```sh
make check                               # Runs the full self-test suite: imports every Python module,
                                         #   byte-compiles, syntax-checks every Bash wrapper, and
                                         #   end-to-end exercises `--help` on all 11 b* commands.
                                         # This is the lint + test command — there is no pytest.
make install DESTDIR=/tmp/dst PYSITELIB=/lib/python3.9/site-packages
                                         # Stage install for inspection.
make rpm                                 # Builds noarch RPM under %_topdir from ~/.rpmmacros (matches
                                         # other smartmet-* repos; same pattern as macgyver, spine).
make clean                               # Removes __pycache__ trees.

./smtop --replay -l /path/to/access-log  # Run the TUI from the source tree (adds . to sys.path).
./smtop -u LABEL=http://host/admin       # Live admin polling, no log file.

SMARTMET_MONITOR_LIB=$(pwd)/share/smartmet/bstat.sh bin/bstat -i 10m file.log
                                         # Run a bstat wrapper from the source tree (the library lookup
                                         # falls back to sibling share/smartmet/ automatically, so this
                                         # env var is only needed if cwd changes).
```

There is no dedicated test runner. When adding code, extend `make check` so new modules/wrappers are exercised by it. For ad-hoc smoke testing the TUI, use a pty harness (`pty.fork` + `select` + `termios.TIOCSWINSZ`) — `curses.wrapper` needs a real terminal.

## Architecture

### `smtop` — Python TUI

Three layers, all running in one process coordinated by `asyncio`:

1. **Data sources** (`smartmet_top/sources/`) write into the store.
   - `logtail.tail_many()` — pure-stdlib `tail -F` across N files; detects rotation by inode. No `tail` subprocess.
   - `adminapi.poll_all()` — one task per host. Fetches `?what=list` on startup + every 5 min, then polls `cachestats`/`servicestats`/`activerequests`/`lastrequests` every `--admin-interval` seconds. Executes HTTP in a shared `ThreadPoolExecutor` so a slow host never blocks others. `lastrequests` feeds into the same URL stats pipeline the log-tailer uses, so the URLs panel works on hosts where logs aren't locally readable.
2. **State** (`smartmet_top/state/store.py`) is the single source of truth.
   - `Store` is thread-safe via a single `RLock`. All sources write; all panels read.
   - **Memory is bounded by design:** per-URL stats are one `Histogram` per minute-bucket (40 exponential bins, base 1.5), retained 60 minutes → ~20 KB per URL. Admin snapshots keep 300 samples per entity per host in a `deque`. If a new feature retains extra data, think about the per-URL cost first.
   - **Everything admin-related is keyed by host label** (`Dict[host, ...]`). Single-host mode is just the multi-host path with one entry. When adding admin data, register the host via `store.register_admin_host(host)`; don't reach into the private dicts.
3. **UI** (`smartmet_top/app.py` + `smartmet_top/panels/`). `App.run()` is a cooperative curses + asyncio loop (redraw every ~300 ms, key poll every ~20 ms). Panels implement three methods only — `draw(win, store)`, `handle_key(key, store)`, `export_snapshot(store) -> (headers, rows)` — defined in `panels/base.py`. Add a panel by dropping a file into `smartmet_top/panels/`, instantiating it in `App.__init__`, and giving it a unique `hotkey`.

Colour is semantic, not decorative. `smartmet_top/theme.py` defines helpers like `latency_color(ms)`, `err_color(pct)`, `hitrate_color(pct)` — panels use those so thresholds stay consistent across views. Rendering rows with mixed per-cell colours uses `panels.base.write_row(cells, row_attr=...)` so the selected-row highlight composes correctly with cell colours.

Frontend vs backend is **runtime-detected**, not compile-time. `adminapi._detect_role()` looks at which `what=` handlers the remote plugin exposes and tags the host `frontend`/`backend`/`mixed`/`unknown`. There is no `--role` flag.

### `bstat` family — shell + awk

All five commands (`bstat`, `bchart`, `burls`, `bstatus`, `bkeys`) and the six legacy aliases (`bstat1s`, `bstat10s`, `bstat1`, `bstat10`, `bstat60`, `bstat24`) are tiny Bash wrappers in `bin/` that do one thing: source `share/smartmet/bstat.sh` and call the matching function. New logic goes in the library, not in the wrappers.

The legacy aliases exist **for gradual rollout across multiple SmartMet installations** and forward every argument to `bstat -i <interval>`. They accept `--ascii` like `bstat` itself (which swaps Unicode eighth-blocks for `=` bars). Do not remove them without a migration plan.

GNU awk specifics to remember: `asort()` returns a 1-indexed array — the library always iterates `for (i=1; i<=n; i++)`. Partial-block characters are stored as associative array entries because indexing UTF-8 bytes via `substr` isn't portable across awk flavours (though this codebase targets GNU awk specifically).

### Access log format

Parsed by both the Python `logparse.py` and the awk programs in `bstat.sh`. Source of truth: `/home/mheiskan/hub/spine/spine/AccessLogger.cpp`.

```
IP - - [END_TIME] "METHOD URL HTTP/VER" STATUS [START_TIME] DUR_MS BYTES "ETAG" APIKEY
```

Field positions by `awk` default splitting: `$1=IP`, `$4=[end]`, `$5="METHOD`, `$6=URL`, `$7=HTTP/ver"`, `$8=status`, `$9=[start]`, `$10=dur_ms`, `$11=bytes`, `$12="etag"`, `$13=apikey`. URLs are URL-encoded and therefore never contain literal spaces in practice.

## Packaging notes

- **RPM name** is `smartmet-monitor`, not `smartmet-tools` (the latter was rejected as too generic; `qdtools`/`shapetools` set the precedent that "tools" is always domain-scoped). The repo directory is still `smartmet-top/` for historical reasons; don't rename it without also updating git remotes.
- The Python package directory is `smartmet_top/` (underscore), which is imported as `smartmet_top`. The binary is `smtop`, with a `smartmet-top` symlink for consistency with the rest of the `smartmet-*` ecosystem.
- The package installs system commands to `/usr/bin`. There is deliberately **no `/etc/profile.d` entry** — these are real commands, not shell aliases sourced at login. An earlier version did use profile.d; that model was replaced.
