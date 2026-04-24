# MAINTENANCE

`smartmet-monitor` is a pure consumer — it parses access logs emitted by
`spine/AccessLogger.cpp` and polls endpoints exposed by the SmartMet
**admin** plugin. It has no influence over those formats, so any
upstream change silently becomes a consumer bug here. This document
lists the upstream surfaces the tool depends on and what to update in
this repo when each one changes.

## 1. Access-log line format

**Source of truth:** `spine/spine/AccessLogger.cpp`

**Format this code assumes (unchanged since the repo was forked):**

```
IP - - [END] "METHOD URL HTTP/VER" STATUS [START] DUR_MS BYTES "ETAG" APIKEY
```

Field positions used by the awk programs (default whitespace splitting):

| awk field | meaning        |
|-----------|----------------|
| `$1`      | IP             |
| `$4`      | `[END]`        |
| `$5`      | `"METHOD`      |
| `$6`      | URL            |
| `$7`      | `HTTP/ver"`    |
| `$8`      | STATUS         |
| `$9`      | `[START]`      |
| `$10`     | DUR_MS         |
| `$11`     | BYTES          |
| `$12`     | `"ETAG"`       |
| `$13`     | APIKEY         |

### Update here when…

- **A field is added, removed, or reordered** in `AccessLogger.cpp`.
- **Field quoting changes** (e.g. ETAG becomes unquoted, or URL gains
  embedded whitespace that breaks the assumption of one whitespace
  token per field).
- **Timestamp formatting changes** (currently `YYYY-MM-DDTHH:MM:SS.mmm`
  local time, no timezone suffix; parsed by
  `datetime.fromisoformat`).

### Where to patch

- `smartmet_top/sources/logparse.py` — `_LINE_RE` regex (line ~24) and
  the `parse()` result dict. One regex, one ordered `m.groups()` tuple.
- `share/smartmet/bstat.sh` — every awk block references fields by
  position: `$8` status, `$9` `[start]`, `$10` dur_ms, `$11` bytes,
  `$6` URL, `$13` apikey. Grep the file for `\$[0-9]` to find all
  call sites when positions shift.
- `CLAUDE.md` and `README.md` both document the format; update the
  reference block there as well so the guidance doesn't rot.

## 2. Admin plugin API

**Source of truth:** `brainstorm/plugins/admin/` (handlers for each
`what=` variant) plus whatever JSON the handlers emit.

### Endpoints polled

Declared in `smartmet_top/sources/adminapi.py` (line ~48, `ENDPOINTS` tuple):

| `what=` name     | Used for                                           |
|------------------|----------------------------------------------------|
| `list`           | One-shot (startup + every 5 min) — handler names used to auto-detect frontend/backend role |
| `cachestats`     | Caches panel                                       |
| `servicestats`   | Services panel                                     |
| `activerequests` | Active panel                                       |
| `lastrequests`   | URL stats fallback when local access logs aren't readable |

All requested with `&format=json`. `lastrequests` is also asked with
`&minutes=1` — if the admin plugin's query parameter changes name,
update the URL in the `ENDPOINTS` tuple.

### JSON field assumptions

The poller tolerates a handful of spelling variants already (Capitalised
and lowercase), but still needs specific keys to exist. Grep-friendly
list with the primary spelling first:

**cachestats rows** (`_ingest_cachestats` in `adminapi.py` ~line 195):
`cache_name`/`name`, `size`, `maxsize`/`max`, `hits/min`/`hits_per_min`,
`inserts/min`/`inserts_per_min`, `hitrate`.

**servicestats rows** (`_ingest_servicestats` ~line 216):
`Handler`/`handler`, `LastMinute`, `LastHour`, `Last24Hours`,
`AverageDuration`.

**lastrequests rows** (`_ingest_lastrequests` ~line 231):
`Time`/`time`, `Duration`/`duration`, `RequestString`/`requeststring`,
`Status`/`status`, `ContentLength`/`contentlength`, `Apikey`/`apikey`.

**list rows** (for role detection ~line 86):
either `What`, `what`, or `name` — whichever is present is accepted.

**activerequests rows:** consumed by `smartmet_top/panels/active.py` —
check that panel if the field names shift.

### Role auto-detection

Hard-coded marker sets in `adminapi._detect_role()` (~line 154):

- **Frontend markers:** `backends`, `clusterinfo`
- **Backend markers:** `qengine`, `producers`, `gridproducers`,
  `obsproducers`, `geonames`, `parameterinfo`, `stations`

If the admin plugin renames any of these handlers or introduces a new
one that unambiguously signals a role, update both sets. The role is
purely a visual cue — getting it wrong degrades the panel header but
doesn't break data ingestion.

### Update here when…

- A `what=` endpoint is **renamed, removed, or gains/loses a query
  parameter** that this tool relies on.
- JSON **field names** in any of the polled handlers change.
- New handlers appear that belong to one role unambiguously (add them
  to the marker sets so role detection stays accurate).
- The admin plugin gains a **new endpoint** that would make a good panel
  — register it in `ENDPOINTS`, add an ingest function, and add or
  extend a panel in `smartmet_top/panels/`.

## 3. Python runtime baseline

**Current floor:** Python 3.9 (declared in `smartmet-monitor.spec`
`Requires:` and `BuildRequires:`, and in the CLAUDE.md).

The codebase is **pure stdlib by design** (see CLAUDE.md §"What this
is"). Adding a PyPI dependency is a policy decision, not a mechanical
one.

### Update here when…

- RHEL/Rocky base image drops Python 3.9 support. Raise the floor in
  the spec and retire any compatibility shims added for 3.9.
- You genuinely need a stdlib feature only present in a later Python
  version — bump the floor rather than backport.
- Someone tries to add a third-party import. Reject unless the stdlib
  truly can't cover it; record the justification in CLAUDE.md.

## 4. GNU awk & Bash assumptions

`share/smartmet/bstat.sh` uses GNU-awk specific features — notably
`asort()` with 1-indexed results. Wrappers in `bin/` target Bash 4+.

### Update here when…

- A deployment target ships a different awk (mawk, BusyBox awk). Port
  the asort usage and any PROCINFO references, or add a `gawk`
  dependency.
- Bash 3 or POSIX-only targets matter. Today they don't — the FMI
  cibase images all ship bash 5.

## 5. CircleCI base images

The hub convention (`macgyver`, `spine`, etc.) is to build RPMs against
`fmidev/smartmet-cibase-{8,10}`. `smartmet-monitor` doesn't ship a
`.circleci/config.yml` yet because it's noarch and has no C++ build —
but if/when CI is added, mirror the sibling layout.

### Update here when…

- A new cibase major arrives (e.g. RHEL 11). Add a matrix entry.
- Python 3.9 is no longer the minimum available across cibase images
  — revise `Requires:` in tandem.

## 6. Install paths and defaults

Defined in `Makefile`:

- Binaries → `$(PREFIX)/bin`
- Shared awk library → `$(PREFIX)/share/smartmet/bstat.sh`
- Python package → `$(PYSITELIB)/smartmet_top/` (site-packages via
  `sysconfig.get_paths()["purelib"]`)
- Man pages → `$(PREFIX)/share/man/man1`

Default log-file glob for `bstat` with no argument: everything matching
`/var/log/smartmet/*-access-log` (in `bstat.sh`).

### Update here when…

- The SmartMet host layout changes where access logs live.
- Python site-packages layout convention changes (unlikely; controlled
  by the distribution).

## 7. Version bumps

`Version:` in `smartmet-monitor.spec` is the canonical release version.
`smartmet_top/__init__.py` exposes `__version__`, and the Makefile reads
that via `sed` to name the source tarball. Keep the two in sync — a
single PR that changes both is less error-prone than relying on memory.

The `%changelog` at the bottom of the spec should gain an entry per
release, matching the sibling-repo style (one bullet per notable
change).

## 8. What to do when something breaks

1. **Log lines stop parsing.** Compare a fresh log line to the regex in
   `logparse.py` and the awk positional references in `bstat.sh`.
   `./smtop --replay -l path/to/log` fails fast and visibly when parsing
   drifts.
2. **Admin panels go empty / say `partial: …`.** The per-host status
   line in the panel footer names the endpoint that failed. Pull the
   raw JSON with `curl 'http://host:8080/admin?what=<name>&format=json'`
   and diff the field names against the list in §2.
3. **Role shows `unknown` everywhere.** The `what=list` response
   changed shape, or the marker sets in `_detect_role()` need adjusting.
4. **`make rpm` emits `File listed twice` noise.** Cosmetic Fedora
   Python-macro interaction; doesn't affect the RPM contents (`rpm
   -qlp` is clean). Worth a real fix in the spec eventually but not a
   correctness bug.
