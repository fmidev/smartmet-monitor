# On RHEL 8 the Python 3.9 AppStream module ships as `python39`; on
# RHEL 10+ and Fedora the package name is `python3`. Pick the right
# one and use the matching rpm-macros package so %%{python3_sitelib}
# resolves to /usr/lib/python3.9/site-packages on RHEL 8 too.
%if 0%{?rhel} == 8
%global python3_pkgversion 39
# On RHEL 8 `/usr/bin/python3` is 3.6 — we must invoke 3.9 explicitly.
%global python3_bin python3.9
%else
%global python3_pkgversion 3
%global python3_bin python3
%endif

%global _python3_sitelib %{python3_sitelib}

Name:           smartmet-monitor
Version:        26.5.2
Release:        9%{?dist}.fmi
Summary:        Log analysis and live monitoring tools for SmartMet Server
License:        MIT
URL:            https://github.com/fmidev/smartmet-monitor
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python%{python3_pkgversion}
BuildRequires:  python%{python3_pkgversion}-rpm-macros
BuildRequires:  make

Requires:       python%{python3_pkgversion}
# bstat.sh uses GNU-awk features (asort), so mawk/busybox awk won't do.
Requires:       gawk
# smtop --perf shells out to `perf` (linux-tools); without it the Proc
# panel still works for memory/IO. Operators who need flamegraphs are
# expected to dnf install perf separately.
Recommends:     perf
# bcc-tools provides offcputime-bpfcc, biolatency-bpfcc and friends
# used by the off-CPU profiler in the Flame view (toggle with `o`)
# and the planned block-I/O / lock-contention panels. RHEL 8 ships
# bcc-tools in the base repos; on Fedora / RHEL 10+ it lives in
# bcc-tools too. The Flame view detects the tool at runtime and
# renders an install hint when it is missing.
Recommends:     bcc-tools
# bpftrace is the scripting alternative used for futex / lock-wait
# stack traces. Optional; the off-CPU view falls back to bcc-tools
# alone when bpftrace is missing.
Recommends:     bpftrace

%description
Two companion tools for operating a SmartMet Server:

  * bstat, bchart, burls, bstatus, bkeys — Bash/awk command-line tools
    for analysing access-log files offline. Installed under /usr/bin/
    and share a common library at /usr/share/smartmet/bstat.sh.

  * smtop — an interactive curses dashboard (like btop) that tails
    access logs and polls the admin plugin for cache statistics,
    service statistics, active requests and more. Supports multiple
    hosts and auto-detects whether each admin URL belongs to a
    frontend or backend node.

The browser-based companion `smwebmon` is shipped as a separate
optional package (`smartmet-webmon`) so this RPM stays free of
extra dependencies for sites that only want the CLI tools.

Both parts are implemented with the Python 3 standard library; no pip
packages are required at runtime.

%prep
%setup -q

%build
# Nothing to compile — Python stdlib only. The Makefile's check target
# validates byte-compilation across all modules. PYTHON= pins the
# interpreter to the 3.9 build even on RHEL 8 where `python3` is 3.6.
make check PYTHON=%{python3_bin} PYSITELIB=%{_python3_sitelib}

%install
rm -rf %{buildroot}
make install \
    DESTDIR=%{buildroot} \
    PREFIX=%{_prefix} \
    PYSITELIB=%{_python3_sitelib}

%files
%{_bindir}/smtop
%{_bindir}/smartmet-top
%{_bindir}/bstat
%{_bindir}/bchart
%{_bindir}/burls
%{_bindir}/bstatus
%{_bindir}/bkeys
%{_bindir}/bperf
# Legacy compatibility aliases. These all call `bstat -i <X>` and
# remain supported so existing operator workflows and documentation
# do not break during gradual rollouts.
%{_bindir}/bstat1s
%{_bindir}/bstat10s
%{_bindir}/bstat1
%{_bindir}/bstat10
%{_bindir}/bstat60
%{_bindir}/bstat24
%{_datadir}/smartmet/bstat.sh
%{_datadir}/smartmet/bperf.py
%{_mandir}/man1/smtop.1*
%{_mandir}/man1/bstat.1*
%{_mandir}/man1/bchart.1*
%{_mandir}/man1/burls.1*
%{_mandir}/man1/bstatus.1*
%{_mandir}/man1/bkeys.1*
%{_mandir}/man1/bperf.1*
%{_mandir}/man1/bstat1s.1*
%{_mandir}/man1/bstat10s.1*
%{_mandir}/man1/bstat1.1*
%{_mandir}/man1/bstat10.1*
%{_mandir}/man1/bstat60.1*
%{_mandir}/man1/bstat24.1*
%doc %{_docdir}/smartmet-monitor/README.md
%doc %{_docdir}/smartmet-monitor/perf-event-paranoid.md
%doc %{_docdir}/smartmet-monitor/images/
%{_python3_sitelib}/smartmet_top/

%changelog
* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-9.fmi
- Co-bumped with smartmet-webmon for the chart hover-tooltip
  coverage on per-row sparklines (Plugins / Services / Caches
  trend cells; Network Connection-states per-state mini-charts).
  See smartmet-webmon changelog.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-8.fmi
- Smtop hotkey case convention enforced: **uppercase letters switch
  panels, lowercase letters are within-panel navigation.** Previously
  the panel-hotkey dispatcher in app.py was case-insensitive, so a
  panel binding lowercase ``n`` (Proc's "next PID") shadowed the
  global ``N`` (→ Network panel). Fix: dispatcher now matches only
  ASCII A-Z. Each panel is free to use lowercase letters for
  per-panel commands without collisions.
- Proc panel restored: lowercase ``n`` cycles the selected smartmetd
  PID (the canonical "next" within-panel binding); uppercase ``N``
  reliably switches to the Network panel; 1-9 still jump to a PID
  by its red ``[N]`` mnemonic. Supersedes the partial fix in -7
  which had removed the ``n``-cycle binding entirely. Help text,
  bottom-of-panel legend, and the file-level docstring updated.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-7.fmi
- Proc panel: ``n`` and ``N`` no longer intercept "next/prev PID".
  Each smartmetd row already carries a red ``[1]`` / ``[2]`` /
  ... mnemonic that picks that PID directly — the n/N intercept
  was redundant and, worse, shadowed the global Network-panel
  hotkey ``n`` so pressing ``n`` while on Proc selected the next
  PID instead of jumping to Network. The 1-9 digit shortcut covers
  every realistic deployment (no SmartMet host runs more than nine
  smartmetd processes). Help text and the bottom-of-panel legend
  updated to match.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-6.fmi
- Default --journal-unit changed from "smartmet-server" to
  "smartmet-backend,smartmet-frontend". The previous default was
  factually wrong: SmartMet's systemd units are named smartmet-backend
  (data daemon) and smartmet-frontend (Sputnik routing daemon), and
  both may coexist on the same physical host. journal_loop now
  accepts a comma-separated list and spawns a single journalctl with
  multiple -u flags so all listed units' lines stream in one
  timestamp-merged feed.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-5.fmi
- Bump smartmet_top.__version__ to 26.5.2 (was lagging at 26.4.30
  through the Phase 2/3 spec bumps). Also fixes make-rpms which
  reads VERSION from __init__.py to name the source tarball.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-4.fmi
- Co-bumped with smartmet-webmon for the 10 s cluster lastrequests
  cache (cuts backend admin-plugin load by ~5×). See smartmet-webmon
  changelog.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-3.fmi
- _build_cluster_chart now omits errored backends from the chart
  series (the legend still surfaces them with a warning marker).
  Backing the cluster multi-line chart UX fix in smartmet-webmon.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-2.fmi
- Co-bumped with smartmet-webmon for the cluster-view Phase 3
  topology strip and README cluster-mode documentation. See
  smartmet-webmon changelog.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-1.fmi
- Co-bumped with smartmet-webmon for the cluster-view Phase 2 wrap-up
  (Plugins / Keys / Overview multi-line per-backend charts). See
  smartmet-webmon changelog.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-17.fmi
- New CachesSnapshot.cluster_chart_per_host /
  ServicesSnapshot.cluster_chart_per_host snapshot methods backing
  the cluster-mode multi-line trend charts in smwebmon (Phase 2b).
- Co-bumped with smartmet-webmon. See smartmet-webmon changelog.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-16.fmi
- Co-bumped with smartmet-webmon for the chart hover tooltip fix
  (no more vertical bouncing; multi-row layout). See smartmet-webmon
  changelog.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-15.fmi
- Co-bumped with smartmet-webmon for the cluster-mode multi-line
  URLs drill-down chart (Phase 2c). See smartmet-webmon changelog.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-14.fmi
- Co-bumped with smartmet-webmon for the cluster-mode multi-line
  Active panel (Phase 2a). See smartmet-webmon changelog.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-13.fmi
- Co-bumped with smartmet-webmon for the cluster auto-detect fix
  (FQDN-based naming was wrong for the FMI deployment because the
  back and internal clusters share the back.smartmet.fmi.fi
  domain). See smartmet-webmon changelog.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-12.fmi
- Co-bumped with smartmet-webmon for the cluster-view groundwork.
  See smartmet-webmon changelog for the multi-cluster discovery,
  registry, selector UI, and auto-detection from the local FQDN.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-11.fmi
- Co-bumped with smartmet-webmon for the chart Y-axis nice-tick
  algorithm switch from qdstat-style 2/5/10 to Heckbert-style
  1/2/5/10. See smartmet-webmon changelog for the why.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-10.fmi
- Co-bumped with smartmet-webmon for the chart Y-axis nice-ticks
  fix. See smartmet-webmon changelog for details.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-9.fmi
- Fixed bulk_load() blocking the asyncio event loop during --replay.
  bulk_load was `async def` but had no await points in its hot path
  — the file-by-line reading was synchronous Python — so the entire
  replay duration was spent inside one coroutine that monopolised
  the loop. Sampler tasks scheduled before replay (per the
  26.4.30-8 reorder) couldn't get CPU until replay finished,
  leaving every Flame mode at its initial-state string ("(disabled
  — start smtop with --perf)" etc.) while perf_enabled itself was
  already true. Each file is now read in a thread-pool executor
  via loop.run_in_executor(); the asyncio loop stays responsive,
  perf_loop and the other samplers tick normally during replay,
  and the HTTP server keeps answering /api/* without the
  multi-second pauses an operator using a browser would notice
  during replay.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-8.fmi
- snapshots/{overview,active,urls,proc}: chart payloads now include
  last_ts + step_seconds (or per-sample ts where available) so the
  web client can map cursor position back to wall-clock time. Pure
  additions to the JSON shape; old clients ignore the extra fields.
- Co-bumped with smartmet-webmon for the unit's CAP_SYS_PTRACE +
  CAP_SYS_ADMIN grant, the kernel-devel Recommends, the
  schedule-samplers-before-replay reorder, and the chart hover +
  Caches/Services column-width UI fixes. See smartmet-webmon
  changelog for details.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-7.fmi
- New reference doc: /usr/share/doc/smartmet-monitor/perf-event-paranoid.md
  covering the kernel.perf_event_paranoid sysctl, what each level
  blocks, which monitor features need which level, the kheaders
  module gotcha for bcc-tools, and the alternative of granting the
  unit kernel capabilities. Linked from the README and the smwebmon
  man page.
- Co-bumped with smartmet-webmon, which now ships
  kernel.perf_event_paranoid=0 + kheaders module-load defaults.
  See smartmet-webmon changelog for details.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-6.fmi
- FlameSnapshot.status() now exposes the full multi-line
  perf_last_error (and the equivalents for off-CPU / page-fault /
  wakeup / blockflame / malloc / biolat / runqlat / perfstat).
  The panel-friendly *_status fields truncate to one short line so
  they fit the panel header; the multi-line errors that perf
  actually emits live in *_last_error, which previously could only
  be read by stepping through the Python store. Now the Flame tab's
  /api/flame/status endpoint returns them and the new "Sampler
  diagnostics" card on the Flame panel renders them verbatim in
  monospace so operators can see what perf actually said, not just
  the panel-friendly first line.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-5.fmi
- Co-bumped with smartmet-webmon for the User=smartmet-server
  default-user fix. See smartmet-webmon changelog for details.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-4.fmi
- Co-bumped with smartmet-webmon for the asset-install fix
  (flame.js wasn't being shipped) and the `--perf` / `--replay`
  default-on changes. See smartmet-webmon changelog.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-3.fmi
- Install rule was shipping a broken RPM. The new
  smartmet_top/snapshots/ subpackage (added in 26.4.28-1 to back the
  smwebmon JSON endpoints) was on disk in the source tree but was
  never listed in the Makefile's install target, so the RPM payload
  did not contain it. `make check` did not catch this because it
  imports from the source tree directly, not from %{python3_sitelib}.
  smtop was unaffected (its panels were also imported from source
  during dev; once installed they failed-to-import too, but no one
  had restarted smtop on a host with the -2 RPM yet). smwebmon hit
  it immediately on first systemctl-start with:

    ModuleNotFoundError: No module named 'smartmet_top.snapshots'

  Fix: switched the install rule to auto-discover subpackages of
  smartmet_top/ instead of listing them explicitly. Adding a future
  subpackage no longer requires a Makefile edit; the same class of
  bug cannot recur.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-2.fmi
- `make check` (and therefore the RPM %build phase) failed on the
  RHEL 8 build host with `urllib.error.HTTPError: HTTP Error 403:
  Forbidden` against the local 127.0.0.1 ephemeral-port test
  server. The cause is the build host's HTTP proxy intercepting
  loopback requests because `no_proxy` doesn't include 127.0.0.1.
  The check's two urllib calls now use a ProxyHandler({}) opener
  to bypass the proxy regardless of the surrounding env. Verified
  by running with a poison `http_proxy` set — the test still
  passes.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-1.fmi
- Build-system fix. `rpmbuild -tb` refuses to build a tarball that
  contains more than one .spec, so `make rpm` and `make rpms`
  failed once smartmet-webmon.spec was added next to
  smartmet-monitor.spec. Reworked the rpm / webmon-rpm / rpms
  targets to stage the source tarball in rpm's %_sourcedir once
  per make invocation, then call `rpmbuild -bb <spec>` per spec
  (the same pattern webmon-rpm was already using). `make rpms`
  now archives HEAD exactly once, runs both rpmbuild calls
  against the staged tarball, and produces both noarch RPMs.

* Tue Apr 28 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.28-3.fmi
- smtop and smwebmon auto-probe the standard SmartMet admin ports
  on localhost when no -u is given: http://localhost:8080/admin
  (frontend) and http://localhost:8081/admin (backend). Whichever
  respond are registered under the labels "frontend" and "backend";
  non-responsive ports are silently skipped (1 s timeout per port).
  On a typical SmartMet host the operator no longer has to type
  -u flags. Explicit -u still wins; --no-admin disables the
  probe entirely. The probe validates the response by checking
  for admin-handler tokens (cachestats / servicestats /
  activerequests / lastrequests) so a stray 200-OK from an
  unrelated service on the same port is not falsely registered.

* Tue Apr 28 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.28-2.fmi
- Snapshots gain detail / chart / trends / top_symbols methods so
  every smtop panel has a richer JSON surface for the web UI.
  Added: OverviewSnapshot.chart (global per-minute series for
  count / mean_ms / p95_ms / bytes / err_pct), ActiveSnapshot.chart
  (aggregated in-flight count history), CachesSnapshot.trends and
  ServicesSnapshot.trends (per-row sparkline series),
  PluginsSnapshot.trends (per-source latency + size series),
  NetworkSnapshot.detail (TCP summary, per-state history, listen
  sockets with recv-Q, per-NIC rx/tx series), ProcSnapshot.detail
  + list_pids (per-PID memory / IO / page-fault / threads time
  series), KeysSnapshot.detail (windowed stats + top URLs by key),
  FlameSnapshot.status / tree / top_symbols (folded stacks across
  modes with smartmet-only / thread-class filters), and a new
  LogsSnapshot for tailing recent_lines. No operator-visible
  change in smtop itself.

* Tue Apr 28 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.28-1.fmi
- New optional companion package `smartmet-webmon` providing a
  browser dashboard (`smwebmon`). To support it, the data-extraction
  side of every smtop panel was lifted out of the curses panel
  classes into a new `smartmet_top.snapshots` package (one module
  per panel). The snapshot is the canonical "what does this panel's
  data look like" — used by smtop's CSV/JSON export today and by
  smwebmon's /api/* JSON endpoints tomorrow. No operator-visible
  change in smtop itself; the refactor is internal.
- New `smartmet_top.runtime` module owning the source-task lifecycle
  (log tail, admin poll, /proc, /proc/net, /proc/vmstat, journal,
  optional perf samplers). App.run() is now a thin wrapper around
  it; smwebmon imports the same function so the two binaries cannot
  drift on which sources are scheduled or how. Behaviour preserved:
  same flags, same defaults, same opt-in gates for --perf and
  --malloc-flame.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-15.fmi
- bperf(1) man page. Matches the bstat-family convention so
  `man bperf` works on installed hosts. Documents pre-flight
  checks, the SmartMet-only / thread-class filters, the three
  output artifacts, and a "How to read the output" section
  per the project's metric-interpretation rule.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-14.fmi
- New `bperf` offline profiler — the batch companion to smtop's
  live Flame view. Runs `perf record -F 99 -g --call-graph=dwarf`
  for N seconds against a smartmetd PID, post-processes the
  captured stacks through the same SmartMet-only and request-vs-
  background filters smtop uses, and writes three artifacts:
    * folded.txt — Brendan-Gregg folded-stack format
    * graph.dot  — GraphViz call graph (render with `dot -Tsvg`)
    * flame.svg  — self-contained click-to-zoom flamegraph
  Default 30s capture, default --threads all, default
  --smartmet-only on. SVG is hand-rolled (~30 lines of inline
  JS) so no flamegraph.pl / inferno runtime dependency is added.
  GraphViz `.dot` is emitted as text — operators run `dot` if /
  when they want a rendered graph, so no graphviz RPM dep either.
  Pre-flight checks: `perf` in PATH, kernel.perf_event_paranoid
  ≤ 2, PID exists; warns when > 50% of frames are `[unknown]`
  (debuginfo not installed).
- Flame panel gains two new filter toggles, both default-on for
  the workflow the user actually wants on day one:
    S — smartmet-only: collapse each stack to its SmartMet
        frames + at most one syscall / libc leaf. Drops the
        libc and kernel scaffolding that crowds out the
        SmartMet code in the unfiltered flame.
    T — thread class: cycle all → request → background. A
        stack is "request" iff it contains
        SmartMetPlugin::callRequestHandler — i.e. that thread
        was actively serving an HTTP request when sampled.
        Stack-content classification is used because spine
        does not pthread_setname_np; every thread shows
        comm=smartmetd and a comm-based filter would be
        useless.
  Both filters compose with the existing on-CPU / off-CPU /
  locks / pagefault / wakeup / blockflame / malloc modes.
  Footer shows the active filter state inline; the panel
  header shows `smartmet-only=on/off thread=all/request/background`.
- Shared filter logic lives in
  smartmet_top/sources/smartmet_filter.py so bperf and the TUI
  panel agree by construction on what counts as a SmartMet
  frame and a request stack.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-13.fmi
- Services panel gains a `cpu%` column showing the fraction
  of avg_ms each handler spends ON CPU. Read from the
  AverageCPUMs field added in spine 26.4.27-2.fmi. Coloured
  by ratio: green ≥ 50% (CPU-bound, on-CPU flame is the
  next stop), blue ≤ 10% (wait-bound, off-CPU flame), neutral
  in between. Renders "—" when polling an older spine that
  does not expose AverageCPUMs, so the operator can tell
  the data is missing rather than zero.
- adminapi parser, panel header / row layout, export_snapshot
  CSV/JSON columns, and the panel_help section all updated
  for the new column. The fixed-left columns block grew by
  6 chars; on terminals at the lower end of the 80-col
  threshold the bar column shrinks automatically per the
  existing `bar_w = max(10, w - fixed_left - trend_w - 4)`
  formula.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-12.fmi
- panel_help backfilled across every remaining panel that
  previously showed only the keys table when `?` was pressed:
  Active, Caches, Services, URLs, Apikeys, Logs, Overview,
  Graphs (Plugins), plus the two composite views Live and
  Health. Each entry explains every column / sparkline /
  drill-in flow on that panel and which dedicated panel to
  switch to for sortable / filterable interaction (composite
  views are display-only). Section-heading detection in
  HelpPanel relaxed to bold any short line ending in `:`
  rather than only all-uppercase lines, so the new headings
  ("Columns:", "Memory:", "Keys:", etc.) render correctly.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-11.fmi
- Context-aware help. `?` (or F1) still toggles the help
  overlay, but it now renders the active panel's contextual
  help FIRST — what every section / metric / sparkline on
  THAT panel actually measures — before the global keys
  reference. The help text is carried by each panel as a
  new `panel_help` class attribute; panels that have not yet
  defined one fall through gracefully ("no contextual help
  written yet"). Initial coverage: Flame, Proc, and Network
  panels — the three densest, most perf-loaded views where
  "what is this number?" comes up most often. Other panels
  (URLs, Caches, Services, Apikeys, Active, Logs, Overview,
  Graphs) are covered by the existing keys table for now;
  contextual help can be backfilled gradually without
  changing any code outside the panels themselves.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-10.fmi
- Per-PID CPU usage in the Proc panel header. /proc/PID/stat
  fields 14 (utime) and 15 (stime) sampled on every poll cycle;
  the panel header now shows "CPU N.NNc (uN.NN sN.NN)" alongside
  uptime and thread count, with N.NN = "fraction of one core
  continuously busy". A reading of 4.0 means smartmetd is using
  4 full cores worth of CPU. The user/system split exposes
  syscall storms as elevated `s` time without the user time
  rising to match.
- Per-HANDLER CPU time (the Graphs panel improvement originally
  proposed) is deferred: it requires a spine-side change to
  ?what=servicestats to expose CPU time alongside the existing
  AverageDuration wall-clock value. The fix is small (~10 lines
  in spine's ServiceStats accumulator) and will land alongside
  the malloc_stats_print spine integration in the next round.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-9.fmi
- systemd-journal tail in the Logs panel. Spawns
  `journalctl -u <UNIT> -n 50 -f` and pushes every line into the
  store as a new [journal] source. The Logs panel's per-source
  tab bar then carries the journal alongside the access-log
  sources, so the operator can flip between "what is smartmetd
  saying" and "what is systemd / the kernel saying about
  smartmetd" in one keystroke. Default unit `smartmet-server`,
  override via `--journal-unit UNIT` or disable with
  `--journal-unit ''`. Auto-recovers if journalctl exits (the
  unit could be transiently absent during a restart).

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-8.fmi
- New Network panel (mnemonic `n`). Pulls the host-wide TCP and
  per-NIC display out of the overcrowded Proc panel and adds:
    * TCP connection-state distribution from /proc/net/tcp{,6}
      with per-state trend sparklines. CLOSE_WAIT > 100 is
      coloured red (socket leak); TIME_WAIT > 5000 is amber
      (ephemeral-port pressure). ESTABLISHED is rendered green
      so the working-set count stays visible at a glance.
    * Listen-socket inspection: every LISTEN port with its
      current accept-queue depth (the Recv-Q `ss -lnt` shows).
      Sustained Recv-Q > 0 is the precursor to listen-drop
      alerts.
    * Per-interface bandwidth — every NIC, not just the busiest
      pair shown in the Proc panel's compact view. rx and tx
      sparklines side by side per row.
  Always-on, no external tools — same /proc/net/* sources as
  the existing netstats sampler.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-7.fmi
- Three new Flame view modes from Brendan Gregg's flamegraph
  catalogue:
    W → wakeup (perf -e sched:sched_wakeup)
    I → block-I/O issue (perf -e block:block_rq_issue)
    A → allocations via bpftrace uprobe on malloc — DEV ONLY
  Wakeup is the dual of off-CPU: it shows who is unblocking
  threads, the standard recipe for finding the *holder* side of
  a lock when off-CPU shows the waiter side. Block-I/O issue
  catches direct reads/writes/fsyncs in addition to page-cache
  misses, complementing the page-fault flame.
- The malloc flame is gated on the explicit --malloc-flame CLI
  flag because uprobe-on-malloc has measurable overhead. Default
  filter min-bytes is 4096 (focuses on operationally interesting
  allocations and drops noise from millions of small allocs);
  --malloc-flame 0 traces every malloc with extreme overhead.
  Allocator (jemalloc, mimalloc, glibc) is auto-detected by
  scanning /proc/PID/maps. Strong warning shown in the panel
  when 'A' is pressed without --malloc-flame, so a junior
  developer cannot accidentally enable it on production by
  pressing the wrong key.
- Wakeup and block-I/O auto-start with --perf since they are
  pure perf and have negligible overhead. Allocation flame
  remains off until --malloc-flame is set.
- Footer shows all seven mode keys with the active mode in
  reverse video. Flame view help text and README updated to
  cover the workflow including the production-safety notes for
  the malloc flame.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-6.fmi
- Flame view: replace the `o` mode-cycle with direct mnemonic
  keys.
    C → on-CPU
    B → off-CPU (Blocked)
    L → off-CPU (Locks)
    M → page-faults (Memory)
  Uppercase so the lowercase panel mnemonics (l=Logs, c=Caches,
  o=Overview, p=Proc) still reach the global panel switcher when
  pressed from the Flame view. Footer shows all four keys with
  the active one in reverse video; `o` no longer cycles, freeing
  it to switch to Overview as it would from any other view.
- README admin smoke-test now lists the wget commands directly
  rather than embedding the wget terminal screenshot. The
  monitor_wget.png file is removed from doc/images.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-5.fmi
- Default smartmetd PID now prefers a process tagged `backend`
  whenever one is detected. The Flame view, Proc panel and
  every perf sampler that targets the focused PID land on a
  backend by default, since backends do the actual data work
  and are what the operator almost always wants to profile.
  Falls back to the lowest PID when no backend is detected.
  The detection is the existing cmdline-based heuristic in
  sources/proc.py — no changes there. New Store helper
  proc_default_pid() centralises the logic so all three
  fallback sites (proc_selected, FlamePanel.draw,
  ProcPanel.draw / handle_key) stay in sync.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-4.fmi
- Two new flame modes: page-fault and off-CPU (locks). The Flame
  view's `o` key now cycles through four modes:
    on-CPU → off-CPU → off-CPU (locks) → page-faults → on-CPU
- New page-fault sampler (sources/pagefault.py) runs
  `perf record -e major-faults -c 1 -ag -p PID -- sleep N` and
  pushes stacks into a parallel ring on the Store. Each sample is
  one synchronous block read — the flame's frame width measures
  fault count per function. Pure perf, auto-starts alongside the
  on-CPU sampler when --perf is set; same access requirements,
  no bcc dependency.
- Off-CPU (locks) is a filter on top of the existing off-CPU
  recorder. Stacks whose leaf matches a known lock-wait symbol
  (futex_*, pthread_mutex_*, pthread_cond_*, pthread_rwlock_*,
  pthread_spin_*, __lll_*) are kept; everything else is dropped.
  Ranks contention points by total wait time using the off-CPU
  recorder's existing microsecond weighting.
- The "Top X functions" list at the bottom of the Flame view
  shifts to match the active mode: top blocked-on functions for
  off-CPU, top contended locks for off-CPU (locks), top
  fault-causing functions for page-faults.
- README "Reading the live monitors" gains a Page-fault
  flamegraph entry and the off-CPU entry now mentions the
  locks-only filter as the natural follow-up.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-3.fmi
- Proc panel sparklines are now multi-row Braille charts by
  default (height = 2). The metric rates and percentile graphs
  carry more actionable diagnostic information than the perf-top
  symbol list at the bottom of the panel, so they get the
  vertical real estate first. Each height = N row gives 4×N
  levels of vertical resolution from the Braille encoding.
- New keys `+` / `-` (and `=` accepted as the un-shifted variant
  of `+`) grow / shrink the sparkline height live within the
  Proc panel; the footer shows the current value. Range 1–6
  rows. Default 2; press `-` once to land back on the previous
  single-row layout, or `+` to use up to 6 rows on a tall
  terminal where the extra resolution is useful.
- Every section in the Proc panel (Memory, I/O, Page faults,
  Page cache + reclaim, Block I/O latency, Run-queue latency,
  CPU efficiency, Network including the per-NIC rows) routes
  its sparkline through a single _draw_spark helper that picks
  vchart() at height > 1 and the existing single-row sparkline
  at height = 1. The Memory section's per-row layout shifts so
  side stats (VmSize / VmPTE / Swap / HWM) anchor to the first
  row of each block, not bumping around with the sparkline
  height.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-2.fmi
- Network section now auto-picks the busiest rx and tx NICs
  rather than rendering a row per interface. On a hosts with
  one uplink the panel shows that NIC labelled `busiest`. When
  rx and tx peak on different interfaces — a storage VLAN
  pulling data in while a public uplink pushes responses out —
  the panel shows two rows labelled `rx-busy` and `tx-busy`
  so neither half is hidden. Selection averages over the last
  ~12 samples so a brief burst on an otherwise quiet NIC does
  not flip the pick. The interface name is always rendered
  next to the label.

* Mon Apr 27 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.27-1.fmi
- Page-cache and reclaim-pressure stats in the Proc panel from
  /proc/vmstat + /proc/meminfo. Four numbers: cache size as a
  percentage of total memory, system-wide major-fault rate
  (complements the per-PID one), kswapd reclaim rate (silent
  background work, dimmed), and direct reclaim rate (sparklined
  in red). Direct reclaim is the operational killer: when an
  application's malloc cannot find a free page the calling
  thread reclaims one itself before the allocation returns,
  adding wall-clock time that no other panel can show. Always-
  on, no external tools.
- New detector vmstats-direct-reclaim raises a warn-severity
  alert as soon as direct reclaim fires for three consecutive
  vmstat windows. Suggests Proc → Page cache as the next look,
  with the multi-line detail body covering the four typical
  root causes (working-set vs min_free_kbytes headroom,
  allocation bursts, swappiness tuning, NUMA imbalance).
- README's "Reading the live monitors" gains a Page-cache entry
  in the Brendan Gregg style. The "Alerts that ship today"
  table picks up vmstats-direct-reclaim.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-11.fmi
- Fix bcc-tools detection on RHEL 8. shutil.which only walks
  $PATH; the bcc-tools package on RHEL 8 installs scripts
  directly to /usr/share/bcc/tools/ (no -bpfcc suffix), and
  /usr/sbin/ is dropped by sudo's default secure_path. Both
  cases produced "off-CPU profiling unavailable" install hints
  even when bcc-tools was correctly installed. The new
  profile_caps._find_bcc_tool() helper falls back from $PATH to
  the canonical install directories used by RHEL, Fedora, and
  Debian/Ubuntu, so the off-CPU flame, biolatency and runqlat
  panels now light up automatically once the package is
  present. The install-hint message also names the directories
  it searched, so when a tool genuinely isn't available the
  operator knows where it would have lived.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-10.fmi
- Doc audit. Updated the Proc panel description in the panel
  listing to enumerate every section it now contains (Memory,
  I/O, Page faults, Block I/O latency, Run-queue latency, CPU
  efficiency, Network, Perf top / Flamegraph, smaps_rollup) —
  the previous wording was from the days before today's metric
  additions. Added the missing key bindings to the Key reference
  excerpt: `o` (on-CPU ↔ off-CPU flame toggle), `!` (alerts
  overlay), and the alerts-overlay arrows / Enter / d / Esc
  bindings. New "Reading the live monitors" entry for the
  off-CPU flame: detects, likely causes when one stack dominates,
  healthy / trouble shapes, what to look at next — same Brendan-
  Gregg structure as the other entries, with a link to
  brendangregg.com/offcpuanalysis.html.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-9.fmi
- Cross-panel alert system. Detectors run against every metric
  source (majflt, biolatency, runqlat, perfstat, netstats, perf
  failures); when a threshold is met, an Alert is raised into a
  central Store and surfaced everywhere at once:
    * always-visible severity-coloured badge in the tab bar,
    * blinking global "NEW ALERT" strip above the active panel
      until the operator acknowledges with `!`,
    * per-panel banner above the panel content when an alert
      names that panel as the place to investigate (no
      per-panel wiring — App carves a row above the panel
      sub-window),
    * `!` opens a modal overlay listing every active alert with
      severity, age, suggested next panel, and the multi-line
      "Detected / Likely causes / What to look at next" body
      already used in the README.
- Each alert carries a stable id, a docs_anchor pointing at the
  matching README "Reading the live monitors" subsection, a
  suggested_panel, and a suggested_action. Enter in the overlay
  jumps to that panel AND dismisses; `d` dismisses without
  jumping; Esc closes. Auto-GC drops alerts whose detector has
  not refired for 60 s, so resolved problems disappear from the
  UI without operator action.
- README gains a "Cross-panel alerts" chapter that documents
  the lifecycle (raised → refreshed → viewed → dismissed →
  cleared), the UI surfaces, and a table of every detector id
  shipping today.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-8.fmi
- CPU efficiency in the Proc panel via `perf stat`. Records
  cycles, instructions, cache-references, cache-misses,
  branch-misses for the focused PID over a short window each
  cycle, derives IPC + cache miss rate + branch miss rate,
  renders next to a sparkline of IPC over the retained ring.
  Pure perf, no bcc — reuses the existing perf dependency.
  Colour bands: IPC < 0.3 / cache-miss > 30% / branch-miss > 5%
  go red; IPC ≥ 1.0 goes green. The README's "Reading the live
  monitors" gains an entry covering the four canonical IPC
  failure modes (memory-bound, LLC overflow, branch
  unpredictability, "everything OK but URLs slow → off-CPU").

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-7.fmi
- Run-queue latency in the Proc panel via runqlat-bpfcc. Same
  scaffolding as biolatency: power-of-2 histogram, 5 s window,
  p50 / p95 / p99 microseconds + total context-switch count,
  rendered next to a p95 sparkline. Critical on virtualised
  hosts where CFS bandwidth controls or noisy neighbours hold
  ready threads off the run queue without showing as CPU
  utilisation. p95 ≥ 1 ms goes red. README "Reading the live
  monitors" gains an entry covering the bare-metal vs VM
  expectations and the cross-references (steal time, cgroup
  cpu.stat, on-CPU flame, URLs latency correlation).

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-6.fmi
- Network monitoring in the Proc panel. Three host-wide counters
  from /proc/net/snmp and /proc/net/netstat (TCP retransmits/s,
  listen-queue overflows/s, listen drops/s) plus per-NIC rx/tx
  bandwidth from /proc/net/dev. No external tools, no eBPF —
  always-on regardless of host config; runs on the smallest VM.
  Loopback is omitted from the NIC list because it is never the
  bottleneck and would crowd the panel on hosts where backend ↔
  frontend talks over local sockets. Retransmits > 1/s and any
  listen drops are coloured red.
- README's "Reading the live monitors" gains a Block-I/O latency
  entry and a Network entry, both written in the Brendan Gregg
  style: detects / likely causes / healthy shape / trouble shape
  / what to look at next. The Major page faults entry is also
  rewritten to use the same explicit Detects + Likely-causes
  structure so an on-call operator can diagnose without leaving
  the README.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-5.fmi
- Major page-fault rate per smartmetd PID. New section in the
  Proc panel that reads /proc/PID/stat field 12 (majflt) on each
  poll, computes the per-second rate, and renders it next to a
  sparkline. The killer SmartMet metric: when a fresh model run
  evicts QueryData pages from cache, the next request to touch
  those mmapped files takes a wave of synchronous block reads,
  invisible to on-CPU profiling. The README's new "Reading the
  live monitors" section documents how to interpret the graph
  in the Brendan Gregg style — healthy shape, trouble shape,
  what to look at next when it goes red.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-4.fmi
- Block-I/O latency in the Proc panel. Periodically runs
  `biolatency-bpfcc INTERVAL 1`, parses the power-of-2 microsecond
  histogram, computes p50 / p95 / p99 plus IOPS, and renders them
  next to a sparkline of p95 over the retained ring (10 minutes
  at 5 s cycle). Host-wide rather than per-PID — biolatency
  operates at the block layer and does not expose a process
  filter — but on a dedicated SmartMet host the dominant block
  I/O is smartmetd anyway. Auto-starts alongside the off-CPU
  sampler when --perf is set; bcc-tools probe gates the loop and
  surfaces an install hint in the Proc panel when it's missing.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-3.fmi
- Off-CPU flamegraph in the Flame view. Press `o` while the panel
  is open to switch between the existing on-CPU flamegraph and a
  new off-CPU one weighted by microseconds-blocked per stack.
  Stacks come from bcc-tools' `offcputime-bpfcc -p PID -f SECS`,
  parsed in folded form so the same flamegraph renderer can build
  the tree from (stack, weight) pairs. Identifies threads
  parked on futex / mutex waits, sleeping on I/O, etc. — the
  classic answer to "the request is slow but on-CPU shows
  nothing".
- The bottom of the Flame view now shows a "Top blocked-on
  functions" list when in off-CPU mode, summing leaf-symbol
  microseconds across the retained stack ring. On-CPU mode keeps
  its existing top-symbols + sparkline list.
- Capability detection added in
  smartmet_top/sources/profile_caps.py: probes for perf, the
  sched_switch tracepoint, offcputime-bpfcc, biolatency-bpfcc,
  and bpftrace, all cached and side-effect-free. Off-CPU loop
  picks bcc by preference, falls back to perf, falls back to a
  panel install hint if neither is available.
- Spec gains Recommends: bcc-tools and Recommends: bpftrace
  alongside the existing Recommends: perf. None are hard requires
  — the off-CPU view degrades gracefully and the rest of the
  smtop / bstat-family functionality is unaffected.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-2.fmi
- Rename the Braille-chart height option from -H back to -h across
  bstat, bchart and bstatus. Lowercase is the conventional case for
  short flags; -h was previously reserved as a help alias, but help
  is fine with --help only and that frees -h for the more useful
  use. The -h help alias is dropped in every bstat-family command;
  --help still works everywhere.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.26-1.fmi
- Switch to the YY.M.D calendar versioning scheme used by every
  other smartmet-* package in the hub (smartmet-library-spine,
  smartmet-library-macgyver, smartmet-library-newbase, …): two-
  digit year, one-or-two-digit month, one-or-two-digit day, with
  Release suffix .fmi. Existing 0.7.x history is preserved below.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.17-1
- Default --replay-bytes raised from 256 MB to 1 GB. SmartMet
  access logs typically rotate daily and busy services hit several
  hundred MB per day; 256 MB consistently failed to cover a full
  day's history, forcing operators to set --replay-bytes 1G by
  hand. Lower it explicitly when startup time matters more than
  history depth.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.16-1
- Flag consistency across the bstat-family commands:
  - -H now means "Braille chart height" everywhere it appears.
    bchart's -h (which used to set the chart height) is renamed to
    -H, freeing -h to mean help; bstatus gained -H for the same
    purpose, controlling the per-class sparkline height (default 4
    char-rows, matching bstat).
  - -h | --help works in every command (previously bchart and bmon
    accepted only --help).
- New time-bucket intervals 2m and 5m. Existing intervals snap to a
  digit boundary (1m, 10m, 1h …) which is why 3m/4m/etc never fit;
  2m and 5m use minute-rounding (round the timestamp's minute field
  down to the nearest 2- or 5-multiple) so they produce a clean
  16-character key without the trailing-"0" workaround.
- bstatus -i: per-class sparklines are now multi-row Braille (4
  char-rows by default, tunable via -H), matching the bstat footer.
  ASCII mode collapses to a single dot-ramp row regardless of -H.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.15-1
- bstat: footer sparklines (requests / latency / avg_size /
  bandwidth) are now multi-row Braille charts. Each sparkline is
  4 character rows tall by default (16 dot rows of vertical
  resolution per metric) instead of 1 row. The topmost char-row
  caps at level 3 so adjacent sparklines retain a 1/4-cell gap;
  rows below stay at full level 4 so a tall bar within a single
  bucket renders as a continuous column. New -H HEIGHT flag tunes
  the per-sparkline height; passing --ascii keeps the original
  single-row dot-ramp layout regardless of -H.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.14-1
- bstat / bstatus / burls / bkeys / bmon: per-row magnitude bars now
  use half-height ▄ (cell-level rounding, no eighth-block partials),
  matching smtop's hbar so a row of bars no longer dominates the
  line height.
- bstat: bottom four sparklines (requests / latency / avg_size /
  bandwidth) switched from vertical eighth-blocks to Braille (2
  buckets per cell, btop-style). Vertical level is capped at 3 of 4
  so the topmost dot row stays empty — keeps adjacent stacked
  sparklines from visually touching.
- bstat / bchart: 10m and 10s buckets render with a trailing "0"
  appended (`13:0` → `13:00`, `13:00:0` → `13:00:00`) so truncated
  ISO-8601 timestamps no longer look mid-digit.
- bchart: vertical chart now renders in Braille by default (4 dot
  rows per char-row × 2 buckets per cell). Adjacent cell-rows of
  the same bucket retain full level-4 encoding for continuity. Pass
  --ascii to fall back to eighth-block bars.
- bstatus: new -i INTERVAL mode prepends a per-class time-bucketed
  Braille sparkline view (one row per HTTP class, sparkline showing
  bucket counts over time). --ascii falls back to dot-ramp.
- burls: keeps the full URL by default — different parameter sets
  (GetMap vs GetCapabilities, producer=foo vs producer=bar) are
  distinct rows. New flags:
  -d LIST   drop listed query-string parameter names before grouping
            (e.g. -d bbox,time collapses GetMaps differing only in
             bbox/time into one row).
  -k LIST   keep only listed parameter names (mutually exclusive
            with -d).
  -L, --list-params
            scan the log and print a frequency table of every
            distinct query-string parameter name.
  -i, --interactive
            print the parameter table, then prompt for a comma-
            separated drop-list and re-run the analysis with that
            filter. Requires a log file argument.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.13-1
- Services panel: tall layout. When there are ≥ 2 rows of room
  per service, each handler's row expands into a multi-row block
  whose trend column becomes a vertical Braille chart instead of
  a single-row sparkline. Same compact-vs-tall heuristic the
  Plugins panel uses. With many services and a short body the
  panel falls back to single-row layout.
- Active panel: in-flight count sparkline at the top. The
  admin-poll loop now records len(activerequests) into a per-host
  bounded ring (active_count_history); the panel renders a 4-row
  Braille vchart at the top with auto-scaling, so a peak of 100+
  active requests stays readable. Header carries `in-flight=N
  peak=N` so the headline numbers are visible even on terminals
  too short for the chart.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.12-1
- Health composite: reorder regions Services / Active / Caches.
  Per-handler load and what's currently in flight are the signals
  operators usually look for first; cache stats drop to the bottom
  since they're rarely the headline issue on a healthy day.
- URLs panel: `KB` and `MB` columns replaced with `avg_sz` /
  `total` rendered via human_bytes(), so a Download plugin
  accumulating chunked-transfer-inflated bytes shows
  `1.2TB` instead of misformatted `1200000.00 MB`. Drill-in
  windowed-stats table gets the same treatment. The underlying
  bytes counter from SmartMet's AccessLogger is unchanged — this
  only fixes the rendering.
- Logs panel: add 0 and > as alternative bindings for "follow
  live tail" alongside Enter and End. Some terminals don't deliver
  curses.KEY_END as the expected code, and these letter keys
  test whether End-key delivery or the implementation is the
  problem. Header help advertises End/0/> follow.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.11-1
- Fix the "TypeError: object of type 'function' has no len()" error
  in the Live composite's embedded Plugins panel. The Graphs panel
  time-axis formatter from 0.7.8 used a ternary-of-lambdas:
      fmt = (lambda t: ... if cond else lambda t: ...)
  which Python parses as a SINGLE lambda whose body is the ternary,
  returning either a string OR a lambda — so when the second branch
  fired, fmt(t) returned the inner lambda and the subsequent
  len(label) blew up on a function. Replaced with an explicit
  if/else picking the strftime format string up front.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.10-1
- Remove ALL vim-style movement bindings from every panel. Cursor
  navigation is now arrow keys only (↑↓←→ + Home/End/PgUp/PgDn).
  Letters that were panel-local movement keys — j, k for cursor,
  g, G for top/bottom, h, l for left/right in the Flame view, n,
  p for next/prev in URLs/Apikeys drill-ins — used to shadow the
  global panel mnemonics, so pressing 'k' from inside any panel
  with a row cursor would move the cursor instead of switching
  to the Apikeys panel. Same for 'g' (Graphs), 'h' (Health), 'l'
  (Logs). Now every global mnemonic letter reaches the panel
  switcher from any panel.
- URLs drill-in: drop the h/t/y section toggles. The 'h' binding
  conflicted with the Health panel mnemonic; rather than remap
  it the histogram, status and API-key sections are simply always
  visible. The 'b' back-binding is also gone (Esc / ← still work).
- Help overlay: rewritten to match the new arrows-only navigation
  policy. Documents 1-9 / n / N for PID selection in Proc / Flame,
  drops the j/k/g/G/h/t/y references.
- Inline footers in URLs and URLs drill-in updated to advertise
  the actual current bindings (↑↓ + [ / ] + Esc/← + e/E + Enter).

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.9-1
- Logs panel now uses ONLY arrow keys for navigation; the vim-style
  h/j/k/l bindings are gone because they were shadowing the global
  panel mnemonics (k = Apikeys, h = Health, l = Logs itself, g =
  Graphs). The operator can now press k from inside Logs and land
  on Apikeys directly instead of having to switch to a different
  panel first. Header help text updated to advertise ←→/↑↓/End/PgUp
  rather than the vim shortcuts.
- Logs panel: split arrow keys cleanly. ←→ cycle the focused source
  (was both ←/↑ for back and →/↓ for next), ↑↓ scroll the log
  buffer one line at a time. PgUp/PgDn page through 20 lines.
  Pressing ↓ or PgDn that lands on scroll=0 also re-enables follow
  so the operator doesn't have to chase the tail with End every
  time.
- Logs panel: short buffers now bottom-anchor like real `tail -F`
  output (lines fill from the bottom of the panel, blank space
  above) instead of top-aligning. Empty buffers show a "(no lines
  for this source yet)" placeholder so the panel doesn't look
  broken when cycling to an idle plugin.
- Add explicit fallback bindings in case some terminals don't
  deliver curses.KEY_END as the expected code: Enter and End all
  jump to live tail. Vim-style G/$ removed for consistency with
  the no-vim-bindings policy.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.8-1
- Fix the "title says 'last 60 min' but data spans 24 hours" bug.
  Both the Overview and Plugins panels were doing
  `from ..state.store import HISTORY_MINUTES`, which binds the
  panel's local name at import time and never updates when
  `set_history_minutes()` is called. Switched to
  `from ..state import store as _store` and read
  `_store.HISTORY_MINUTES` dynamically. Same fix applied to
  HISTORY_SECONDS in the Plugins panel.
- Overview panel: time axis now shows clock-time HH:MM labels
  at the left, midpoint and right edges of each chart instead
  of relative offsets. Anchored to local time so axis labels
  match the SmartMet log timestamps.
- Graphs panel: added a shared HH:MM time axis row at the bottom
  of the panel under both the response-time and response-size
  spark columns. Uses HH:MM:SS for sub-minute (60s) windows so
  the labels remain meaningful at second resolution.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.7-1
- Caches and Services panels: horizontal bars now render with `▄`
  (lower half block) instead of `█` (full block), so adjacent rows
  of bars no longer fuse into one solid wall vertically. ASCII mode
  uses `=` for the same effect. Sub-cell horizontal precision goes
  from eighth-blocks to whole-cells, but the indicators these bars
  represent (cache hit %, share-of-load) read fine at cell-level.
- Graphs panel: tall layout. When there's enough room per plugin
  (≥ 3 rows), each plugin's row expands into a multi-row block:
  the name + numeric stats sit on the top row, two vertical Braille
  charts (response time + response size) span all `per_plugin` rows
  on the right. Each plugin's pattern is far more readable than
  the previous one-row-per-plugin layout. With many plugins (e.g.
  the Live composite's 22 sources) per_plugin drops to 1 and the
  panel falls back to the compact single-row layout.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.6-1
- Plugins panel: fix the cursor=-1 case in Live composite. The
  embedded panel passes default_cursor=-1 to suppress the highlight,
  but the cursor-keeps-the-scroll-in-view math wasn't checking for
  this sentinel and pulled `scroll` negative — Python slicing
  rows[-1 : -1+body_h] on a 22-row list evaluates to rows[21:18],
  i.e. empty. Result: the Live panel showed no plugin rows even
  with hide_idle=False. Now the cursor adjustment is gated on
  `cursor >= 0` and `scroll` is clamped into [0, max_scroll]
  regardless.
- Default --history-minutes raised from 60 to 1440 (24 hours).
  ~17 MB extra retention on a 20-plugin host. The Overview chart
  now has a day's worth of data to draw on by default; for less
  memory use --history-minutes 60 (or smaller), for a week
  --history-minutes 10080.
- Overview panel: average-downsamples the full retained history
  into chart_w + 1 samples so the WHOLE retention is visible
  compressed to fit the terminal, rather than only the last
  chart_w + 1 minutes. New widgets/bars.downsample_avg() helper.
  Title and time-axis labels now read "(last 24h)", "-12h" etc.
  when history is in hours.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.5-1
- Live composite: the embedded Plugins panel now passes
  hide_idle=False, so every tailed plugin appears in the row list
  even when its current-window count is zero. The previous default
  (inherited from the dedicated Graphs panel) crushed Live down to
  whichever single plugin happened to be active in the window —
  reported as "shows only the trajectory plugin". The dedicated
  Graphs panel still hides idles by default; the operator can
  press `i` to toggle.

- Overview panel: redesign. The four mini-charts now stack
  vertically full-width instead of cramming side-by-side at ~30
  chars each, the duplicate "requests/min" sparkline at the bottom
  is gone (was rendering the same data as the req/min chart), and
  the time span uses --history-minutes (was hardcoded 60). With
  --history-minutes 10080 the Overview shows a full week of
  per-minute trend at panel-wide resolution. Time-axis labels
  ("-60m", "-30m", "now") appear under the bottom chart so the
  scale is unambiguous.

- Logs panel: per-source ring buffers + arrow-key navigation.
  Each tailed plugin gets its own bounded ring (2000 lines each)
  in the Store, plus the existing global merged ring stays for the
  "[all]" view. The Logs panel now shows a tab bar of plugin names
  at the top with the focused entry marked ▶plugin◀; ←↑→↓ cycle
  the focus, Enter / End jump to the live tail of that source, /
  filters within it, Esc clears the filter. The previous design
  was a single merged stream where a high-rate plugin (wms at
  ~250 req/s) flushed every other plugin's lines out of the
  buffer within seconds, which is what manifested as "Logs panel
  shows only one plugin's logs".

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.4-1
- Live composite: bump the embedded Plugins panel's default window
  from 5m to 60m. The previous 5m window crushed the row list down
  to whichever plugin had the most recent activity (often just
  `textgen`); 60m surfaces every plugin that has had any activity
  in the last hour, which is what the Live overview is for. The
  dedicated Graphs panel still defaults to 60s for live monitoring.
- Logs panel: bump the recent-lines ring from 2000 to 20000.
  On a busy production host one plugin (typically wms at ~250
  req/s) used to crowd out every other plugin's lines from the
  buffer within ~8 seconds, so the panel effectively showed
  one-plugin output. 20000 keeps ~80 seconds of dense traffic
  visible, so other plugins' lines are reliably interleaved.
- Logs panel: add `n` / `N` keys to cycle the filter through the
  tailed plugin labels (uses the bracketed `[plugin]` form so it
  doesn't accidentally match URLs containing the same substring).
  Cycle includes a virtual "all" entry so the operator can clear
  the filter by cycling. Esc still clears it directly. The header
  reads "filter:<all>" instead of "filter:<none>" when no filter
  is active, to be consistent with the cycle-through-all UX.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.3-1
- Per-plugin sparks in Plugins/Live panels now scale to each row's
  own data range, not a column-wide max. The previous shared max
  meant a high-traffic plugin like wms saturated its spark to full
  while every other plugin (timeseries, wfs, ...) got crushed to
  near-empty. Now each row's pattern is visible regardless of the
  absolute magnitude difference; the numeric columns carry the
  comparable magnitudes.
- Live composite no longer renders a cursor highlight on the
  embedded Plugins panel. The selection indicator was misleading
  because Live is display-only and there's no way to actually
  change the cursor from inside the composite. (`default_cursor=-1`
  on the embedded PluginsPanel.)
- Plugins panel: pressing Enter on a plugin row now switches to
  the URLs panel filtered by that plugin's label. The URLs panel
  shows the URL endpoints under that plugin so the operator can
  trace which specific paths are slow / busy. Cross-panel drill-in
  goes through a new store.pending_panel_switch field that the App
  consumes after each key event.
- URLs panel: same auto-widen behaviour the Plugins panel got in
  0.5.6. When the selected window has no URL data — common right
  after --replay because recent log activity may all be older than
  e.g. 5 minutes — the panel auto-widens to the next window with
  data. Header reads "window:5m→60m(auto-widened)" so the swap is
  visible.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.2-1
- parse_iso: handle SmartMet's comma decimal separator. The
  AccessLogger emits timestamps like `2026-04-25T19:57:49,567645`,
  but Python <3.11's `datetime.fromisoformat` only accepts dot
  separators, so on the RHEL 8 build (Python 3.9) every replayed
  line hit the ValueError branch and fell back to `time.time()`.
  Result: every replayed request got assigned the current wall-clock
  instant, all crowded into one minute bucket, and the Overview /
  Graphs panels looked like the last 60 minutes were empty even
  with --replay. Now we normalise the comma to a dot before
  parsing, so the timestamp from the log line is used.
- make check: assert parse_iso parses the comma form identically
  to the dot form, so a future regression on this would fail the
  build.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.1-1
- Fix the Braille bit-to-dot mapping. The previous tables had bit 3
  mapped to dot 7 (bottom-left) and bit 6 mapped to dot 4 (top-right),
  which is the OPPOSITE of the Unicode standard. Every partial Braille
  cell rendered the wrong silhouette — "level 1 left" placed a single
  dot at the *top-right* of the cell instead of the bottom-left.
  Fully-filled cells (4,4) happened to render correctly because all
  bits get set regardless of which position they map to, which is why
  the bug went unnoticed during early testing of constant-maximum
  data but appeared as "vertical gaps" once partial fills became
  common.
- The cell encoding is now verified to match btop's graph_symbols
  table exactly (all 25 (left, right) combinations); a unit-style
  comparison runs in `make check`. Bar charts and sparklines now
  render the silhouette they should: dots fill from the bottom of
  each column upward, transitions read as smooth rising/falling
  ramps, and fully-filled bars look continuous.
- Drop the experimental block-character fallback (0.7.0). It was
  introduced to compensate for the incorrect Braille rendering and
  is unnecessary now that the Braille itself is correct.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.7.0-1
- Three issues you flagged in one testing session:

  * Graphs panel showed only numeric stats, no graphs. The panel was
    asking for `span` data points (e.g. 5 for a 5m window) but the
    spark column was ~35 chars wide, so the visible spark was 5 chars
    of data and 30 chars of leading-zero padding, which can render as
    invisible Braille on some fonts. The panel now asks for
    `spark_w + 1` samples (capped at HISTORY_SECONDS / HISTORY_MINUTES)
    so the column is filled, and the column header advertises the
    actual span being drawn ("(35m)" instead of "(5m)").

  * "Vertical gaps" in Braille graphs. Even a fully-filled Braille
    cell (⣿) renders as four dots stacked with whitespace between
    each dot pair — never a solid bar. _braille_cell now falls
    through to solid block characters when both halves of a cell are
    saturated: (4,4) → █, (4,0) → ▌, (0,4) → ▐. Partial fills (1-3)
    keep the Braille rendering for sub-cell vertical resolution.
    Fully-filled bars now look like solid bars without internal
    whitespace; tall bars in vchart and the Plugins/Overview sparks
    benefit immediately.

  * Logs panel looked like one log was being shown. It actually was
    `tail -F` over every tailed access log merged into a single
    stream, but the lines weren't labelled with their source. Each
    line is now prefixed with `[<plugin>]` so the multi-log nature
    is visible, and the panel header reads "tail -F across N log
    files". The existing `/` filter can be used to narrow to one
    plugin (`/[wms]` or just `/wms`).

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.6.1-1
- Services panel (and the Health composite that embeds it): the
  req/min, req/h and req/d columns are integer counts upstream from
  the admin plugin, so render them as integers. Previously the
  one-decimal formatting added a trailing ".0" to every value, which
  was visual noise without conveying any extra information. The
  CSV/JSON export already used the floats unchanged so this is a
  display-only change.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.6.0-1
- Flame and Proc panels now show a numbered PID selector at the very
  top, one row per smartmetd process, displaying the PID, the
  detected role and the FULL command line. Operators can tell
  frontend from backend at a glance (the cmdline reveals which
  config file each process is using) instead of having to know
  port assignments. The currently-selected process is drawn with
  reverse video.
- Direct PID selection: keys 1-9 jump straight to the PID at that
  index in the list (the [N] markers are highlighted red as the
  shortcut). n/N still cycle. Beyond 9 PIDs (very unusual on a
  smartmet host), n/N is the only way.
- Drop the redundant cmdline-on-row-1 in the Proc panel since the
  cmdline is now shown for every PID in the selector at the top.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.5.6-1
- Live view: the embedded Plugins panel now defaults to the 5-minute
  window instead of 60s. The 60s window can only be filled from live
  tail (it doesn't survive --replay), so right after startup it
  showed "0/22 log files" until 60s of fresh tail accumulated. The
  composite is display-only with no key to widen, so 5m is the
  right default for it.
- Plugins panel: when the operator's selected window has no data
  (common right after --replay) the panel auto-widens to the next
  window that does, and the header shows both the user's selection
  and the effective rendered window — e.g. "window:60s→5m
  (auto-widened)" — so nothing is silently swapped underneath.
- Overview panel: the four mini-charts now have Y-axis labels at
  top, middle and bottom rows so the operator can read the value
  scale at a glance instead of guessing where the bars sit.
- Rename Keys panel to Apikeys to clarify what aggregates by what.
  Keyboard shortcut stays `k` (the K in "Apikeys" is highlighted
  red in the tab label as the mnemonic).

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.5.5-1
- Services and Caches panels: horizontal bar widths now adapt to the
  available terminal width instead of being hardcoded. The previous
  fixed widths (25 for Services, 20 for Caches) wasted ~15+ columns
  on a 140-col terminal and crushed bars for low-traffic services
  down to invisible against the high-traffic ones. Fixed columns
  on the left (handler/cache name + numeric stats) keep their
  widths; the bar absorbs whatever's left after reserving 20 cols
  for the trend sparkline and a 4-col margin. Bars have a 10-col
  minimum to stay readable on narrow terminals.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.5.4-1
- Braille graphs (sparkline + vchart) now use overlapping pairs, the
  same trick btop's graphs use: each character's right column equals
  the next character's left column, so adjacent cells share a data
  point and the graph reads as a continuous shape rather than a
  sequence of stepped pairs. The previous non-overlap encoding
  produced visible gaps and zigzags at every other character
  boundary; you flagged those as suspicious. The trade is that
  width=W now displays W+1 samples instead of 2*W, but visual
  smoothness is the right priority for a live dashboard.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.5.3-1
- Flame view: pressing `s` now opens a centered modal overlay
  listing six preset perf record durations (1, 3, 5, 10, 20, 30
  seconds). ↑↓ navigate, Enter applies and closes; Esc / s / q
  cancels. The applied value lives on the Store as
  perf_record_seconds and the perf sampler picks it up on the next
  cycle, so changes take effect without restarting smtop. The
  current value is marked with a bullet (●) in the list, and
  out-of-band starting values (e.g. --perf-record-seconds 7)
  position the cursor on the closest preset.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.5.2-1
- Triple the default perf record duration from 1 second to 3 seconds
  per cycle. With -F 99 the previous default produced ~99 samples per
  CPU-second, which felt sparse on busy backends — flamegraphs had
  many one-cell tails. The new default gives ~3x more samples per
  flamegraph while keeping the duty cycle at ~30% (3s recording in
  the default 10s window).
- New --perf-record-seconds N flag exposes this for tuning. Larger
  values give denser flamegraphs at proportionally more CPU overhead
  on the target during the recording window.
- Build the flame tree from the entire retained stack ring (~20000
  samples bounded by the store) instead of the most recent 2000.
  The 2000-sample slice was a leftover from when the ring was new
  and we were paranoid about CPU cost; tree construction is fast,
  and using the full ring fills out infrequent paths so they no
  longer disappear from the flame.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.5.1-1
- Switch perf record from frame-pointer call-graph (-g, the default)
  to `--call-graph=dwarf,32768`. SmartMet Server has deep call stacks
  that the default frame-pointer unwinding splits — partial stacks
  show up in the flamegraph as separate trees rooted somewhere in
  the middle of the call hierarchy instead of at `main`. DWARF
  unwinding reconstructs the full chain reliably. The 32 KB
  stack-dump size (default is 8 KB) is sized for those deep stacks
  so perf doesn't truncate them. perf.data files grow several-fold
  but each cycle overwrites the same /tmp file.

* Sun Apr 26 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.5.0-1
- Flame view: stop wasting the rows below the flame tree. Production
  stacks are typically 8-15 frames deep, so on a 40-row terminal half
  the screen was blank. The view now caps the flame to the upper
  portion and fills the lower portion with the same top-symbols list
  the Proc panel shows (mean / p95 sample sparklines included).
- Flame view: interactive zoom. Cursor keys navigate the tree —
  ↑ parent, ↓ most-used child, ←/→ previous/next sibling at the
  same depth — and the selected frame is highlighted. Enter zooms
  in (selected frame becomes the new visible root); Esc / u zooms
  out one level; 0 / Home resets all the way back to the global
  root. The tab header carries a breadcrumb (`zoom=main > engine_run
  > Q::values`) so the operator always knows where they are.
- Flame zoom degrades gracefully: if the zoomed function disappears
  from the most recent stacks (because the program moved on), the
  view walks the zoom path back up until it finds something to
  render rather than going blank.

* Sat Apr 25 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.4.0-1
- Plugins panel: add window selector. `[` and `]` cycle through 60s
  (live, per-second), 1m, 5m, 15m, 60m. The 60s mode is unchanged
  and still the default; the minute modes read the per-minute
  buckets that --replay populates, so the panel is no longer empty
  immediately after replay finishes.
- New --replay-bytes N flag controls the per-file byte cap during
  --replay (default 256 MB, same as before). Raise on low-traffic
  logs that need a longer history; lower on busy logs to reduce
  startup time.
- New --include-rotated flag walks each log path's rotated siblings
  during --replay. Detects both `<base>-YYYYMMDD` (uncompressed,
  freshly rotated) and `<base>-YYYYMMDD.gz` (compressed); when the
  same date appears in both states the .gz is preferred so a
  mid-rotation snapshot doesn't get double-counted. gzip files are
  read transparently via the stdlib `gzip` module — still no pip
  dependency.
- New --history-minutes N flag controls the per-minute retention
  window. Default 60 minutes (unchanged); raise to 1440 for a day
  or 10080 for a week of bucketed history. Memory grows roughly
  linearly with the window: ~12 KB per minute on a 20-plugin host,
  so 24h ≈ 17 MB and 7d ≈ 120 MB.

* Sat Apr 25 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.3.2-1
- perf record: drop -q (which silenced the very error messages we
  needed to diagnose failure) and drop --no-children (a perf report
  flag that some perf builds reject from `perf record`, manifesting
  as a bare "record failed" with no detail).
- perf record failure now stores the full perf stderr+stdout in a
  new store.perf_last_error field. The Proc panel's perf-top and
  flamegraph sub-sections, and the dedicated Flame view, render the
  whole diagnostic in red so the operator can act on it instead of
  guessing. The status line still carries a one-line summary
  including the perf exit code.

* Sat Apr 25 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.3.1-1
- Add Health composite view (mnemonic h): Caches + Services + Active
  stacked in equal thirds. Operator goal: "is this server healthy?"
- Add Flame view (mnemonic f): full-screen live flamegraph for the
  focused smartmetd PID. Reuses the tree builder + renderer from the
  Proc panel but gives them the entire terminal so deep stacks stay
  readable. Requires --perf. The Proc panel keeps its inline
  flamegraph toggle for the case where flame + memory should be
  compared side by side.
- Flame view's "disabled" state now shows store.perf_status verbatim
  ("perf not found in PATH", "(disabled — start smtop with --perf)",
  etc.) and lists the three common causes in the body text rather
  than a single generic message.

* Sat Apr 25 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.3.0-1
- First step toward btop-style multi-panel layouts. Add a Live view
  (mnemonic i) that renders the Graphs panel (top 60%) and the URLs
  panel (bottom 40%) at the same time, each in a derwin'd sub-window.
  This is the new default startup view when log files are configured;
  the dedicated single-panel views (g, u, ...) remain available for
  sortable / filterable interaction. Composite views are display-only
  in 0.3 — focus management between sub-regions will land in a later
  release once the layouts settle from operator use.

* Sat Apr 25 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.2.1-1
- adminapi: when the admin plugin returns a non-JSON body, surface the
  URL, Content-Type and a 120-byte body preview in the error rather
  than a bare JSONDecodeError at column 0. Strip a trailing slash off
  the base URL so `-u http://host/admin/` composes into a valid query.
  Accept both shapes the `?what=list` endpoint emits (list of dicts
  and list of strings).

* Sat Apr 25 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.2.0-1
- smtop charts now render in Braille (U+2800..U+28FF) by default,
  packing two samples per cell at four-step vertical resolution.
  Pass --ascii to fall back to eighth-block characters on terminals
  or fonts without proper Braille glyphs.
- Replace digit panel hotkeys with single-letter mnemonics. Each
  panel's tab label highlights one red+bold+underlined letter
  (o/g/u/c/s/a/p/l/k); per-panel keys take priority via a new
  delegate-first key-handling contract.
- New Graphs panel (mnemonic g): live per-plugin access-log monitor.
  One row per *-access-log file with req/s, mean/p95 latency, error
  percentage and two independently auto-scaling Braille sparklines
  (response time + response size) at one-second resolution over the
  last minute. m/b/i toggle the spark metrics and idle-handler
  visibility. This is the new default startup view when log files
  are configured.
- New Proc panel (mnemonic p): per-smartmetd-PID memory + I/O from
  the cheap O(1) /proc counters. Discovers PIDs via /proc/*/comm,
  splits RSS into file/anon/shmem with per-row Braille sparklines,
  surfaces VmSize, VmPTE, Swap, HWM, FD count and threads. The
  expensive /proc/PID/maps and /proc/PID/smaps are deliberately not
  read at all; smaps_rollup is gated behind an explicit r keypress
  because smartmetd routinely keeps over a million mappings open.
  n/N cycles between processes when more than one smartmetd is
  running on the host.
- New --perf flag: spawns `perf record -F 99 -g -p PID -- sleep 1`
  periodically (default ten-second cycle, ~10% duty) against the
  focused PID, parses `perf script` output, and renders the top
  symbols with per-symbol Braille time-series sparklines in the
  Proc panel. f toggles a live flamegraph view rendered as nested
  width-proportional boxes with stable per-symbol colors that
  rebuilds every cycle. New --perf-interval flag tunes the cycle.
- smtop now starts even with no log files or admin URLs configured,
  so the Proc panel can be used standalone on hosts where the
  operator only wants to watch smartmetd memory or perf data.
- spec: add Recommends: perf for the new flamegraph use case.

* Thu Apr 23 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.1.0-1
- Initial release. Bundles bstat and the new smtop dashboard.
