# smartmet-webmon — browser-dashboard companion to smartmet-monitor.
#
# Ships a single daemon (`smwebmon`) plus the static HTML/CSS/JS it
# serves over HTTP. The shared data-collection layer (sources,
# state.Store, snapshots) lives in smartmet-monitor and is depended
# on at the exact-version level.
#
# The unit is shipped DISABLED by default. See the cost-analysis
# discussion in the project's planning notes; operators run
# `sudo systemctl start smartmet-webmon` only when they want the
# dashboard, then SSH-tunnel to localhost.

%if 0%{?rhel} == 8
%global python3_pkgversion 39
%global python3_bin python3.9
%else
%global python3_pkgversion 3
%global python3_bin python3
%endif

%global _python3_sitelib %{python3_sitelib}

Name:           smartmet-webmon
Version:        26.5.4
Release:        6%{?dist}.fmi
Summary:        Browser dashboard for SmartMet Server (smwebmon)
License:        MIT
URL:            https://github.com/fmidev/smartmet-monitor
Source0:        smartmet-monitor-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python%{python3_pkgversion}
BuildRequires:  python%{python3_pkgversion}-rpm-macros
BuildRequires:  make
BuildRequires:  systemd-rpm-macros

# Exact-version dep — webmon imports smartmet_top.snapshots,
# smartmet_top.runtime, smartmet_top.state.store at runtime, all of
# which live in smartmet-monitor.
Requires:       smartmet-monitor = %{version}-%{release}
Requires:       python%{python3_pkgversion}
%{?systemd_requires}

# Pulled in for the bcc-tools flame modes (off-CPU / biolat /
# runqlat). Those tools chdir into /lib/modules/$(uname -r)/build/
# at startup; that symlink only exists when kernel-devel for the
# running kernel is installed. The kheaders module (which the
# package's modules-load.d entry pre-loads) is a separate header
# source that bcc on RHEL 8 doesn't yet consume. kernel-devel-
# uname-r is a meta-package that auto-resolves to the correct
# kernel-devel-X.Y.Z for the running kernel.
Recommends:     kernel-devel-uname-r

# The unit runs as `smartmet-server` so it can profile smartmetd
# (cross-uid perf record is denied at the default
# kernel.perf_event_paranoid=2) and read the access logs the
# daemon writes. The smartmet-server user is created by the
# smartmet-server RPM; declare a Recommends so packagers can pull
# it in by default without making it a hard requirement (some
# sites build smartmet-server from source under a different name).
Recommends:     smartmet-server

%description
Browser-based companion to smartmet-monitor. Adds the `smwebmon`
daemon that serves the same per-panel data smtop renders, plus an
interactive Canvas flame graph, over HTTP+JSON on loopback. Reuses
the data-collection layer from smartmet-monitor; does not pull X11
or any third-party Python packages.

This package is for operators who study the dashboard's results.
Installing it makes one host-level change the dashboard needs to
work fully:

  * /usr/lib/modules-load.d/smartmet-perf.conf pre-loads the
    kheaders kernel module so bcc-tools (offcputime-bpfcc,
    biolatency-bpfcc, runqlat-bpfcc) can run without root.

The Flame panel additionally needs kernel.perf_event_paranoid <= 0
for an unprivileged daemon to attach hardware perf counters and
tracepoints. That setting belongs to the host's hardening baseline,
not to a monitoring tool — the smartmet-monitor RPM ships an
already-staged drop-in file at
/usr/lib/sysctl.d/99-smartmet-perf.conf with the line commented out;
the operator uncomments it (or copies it to /etc/sysctl.d/) once
they have agreed the change with whoever owns host security policy.
The full reasoning, per-feature compatibility table, and security
trade-offs are in
/usr/share/doc/smartmet-monitor/perf-event-paranoid.md.

The unit runs as the `smartmet-server` user (the same user that
owns the smartmetd processes). Override via a drop-in
(`sudo systemctl edit smartmet-webmon`) if your deployment uses a
different operator account.

The unit is shipped disabled. Start when needed:

    sudo systemctl start smartmet-webmon

then tunnel from your laptop:

    ssh -L 8765:localhost:8765 host

and open http://localhost:8765/ in any modern browser.

%prep
%setup -q -n smartmet-monitor-%{version}

%build
# Nothing to compile — the make check target in smartmet-monitor
# already validated the shared library; this spec just installs the
# webmon-specific files. We re-run check here too to catch the
# webmon imports.
make check PYTHON=%{python3_bin} PYSITELIB=%{_python3_sitelib}

%install
rm -rf %{buildroot}
make install-webmon \
    DESTDIR=%{buildroot} \
    PREFIX=%{_prefix} \
    PYSITELIB=%{_python3_sitelib} \
    UNITDIR=%{_unitdir} \
    SYSCONFDIR=%{_sysconfdir}

%post
%systemd_post smartmet-webmon.service
# Load kheaders without requiring a reboot. Persistent across reboots
# via /usr/lib/modules-load.d/smartmet-perf.conf. The || : guard
# keeps package install from failing on kernels that don't expose
# the module; the dashboard will surface the resulting per-sampler
# errors via /api/flame/status. The perf paranoid sysctl is shipped
# (commented out) by smartmet-monitor and intentionally not applied
# here — that knob belongs to the host's hardening baseline owner.
modprobe kheaders >/dev/null 2>&1 || :

%preun
%systemd_preun smartmet-webmon.service

%postun
%systemd_postun_with_restart smartmet-webmon.service

%files
%{_bindir}/smwebmon
%{_datadir}/smartmet/webmon/
%{_unitdir}/smartmet-webmon.service
%config(noreplace) %{_sysconfdir}/sysconfig/smartmet-webmon
%dir %{_sysconfdir}/smartmet-webmon
%config(noreplace) %{_sysconfdir}/smartmet-webmon/clusters.conf
%{_prefix}/lib/modules-load.d/smartmet-perf.conf
%{_python3_sitelib}/smartmet_webmon/
%{_mandir}/man1/smwebmon.1*

%changelog
* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-6.fmi
- Cache-Control: no-store on the static asset responses
  (server.py). The handler previously sent no Cache-Control on
  HTML/JS/CSS, so browsers used heuristic caching and kept
  serving the previous flame.js / app.js / style.css after an
  RPM upgrade — turning every "bug fix shipped" into "but the
  user thinks it didn't". no-store on small files served over
  loopback has negligible cost; closes a class of stale-asset
  confusion permanently.
- smwebmon unit gains CAP_IPC_LOCK and CAP_SYSLOG. CAP_IPC_LOCK
  fixes off-CPU's "mmap: Operation not permitted" — perf_event_open's
  mmap path mlocks the per-CPU ring buffer beyond
  perf_event_mlock_kb's default 516 KB, which an unprivileged
  daemon can't do without it. CAP_SYSLOG bypasses
  kernel.kptr_restrict=1 (the RHEL default) so /proc/kallsyms
  resolves and perf doesn't bail at exit 255. Both are strictly
  narrower than the CAP_SYS_ADMIN already in the unit; same
  justification chain as CAP_DAC_READ_SEARCH from 26.5.4-5.
- Flame panel diagnostics block now scoped to the currently-
  selected mode. Previously every sampler's failure surfaced
  in every flame mode's diagnostics — runqlat / biolat /
  perfstat (which aren't even flame modes) appeared under
  on-CPU, which made no sense in that context. Each mode now
  shows only its own sampler; biolat / runqlat / perfstat
  failures continue to surface in the Proc panel where they
  belong.
- Flame zoom-out is now discoverable. Three new affordances:
  right-click anywhere on the canvas pops one level up
  (matches flamegraph.com / Speedscope convention); a visible
  "← Zoom out" button next to the breadcrumb does the same;
  the breadcrumb itself got bolder styling — slightly larger,
  full-color text instead of muted, and a subtle background
  strip — so operators stop missing it. The breadcrumb's root
  link still does "all the way out".
- Existing webmon installs picking this up need a
  `systemctl daemon-reload && systemctl restart smartmet-webmon`
  for the new caps to apply, and a force-reload of the
  dashboard (Ctrl+Shift+R) once for the browser to drop its
  pre-no-store cache of flame.js / app.js / style.css.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-5.fmi
- Add CAP_DAC_READ_SEARCH to the smwebmon unit's AmbientCapabilities
  / CapabilityBoundingSet. /sys/kernel/debug is mode 700 root:root
  on RHEL, and CAP_SYS_ADMIN (already granted) does not bypass DAC
  checks — so the bcc-tools that create kprobes (offcputime-bpfcc,
  biolat-bpfcc, runqlat-bpfcc) failed with `open(...kprobe_events):
  Permission denied` even after paranoid was lowered. CAP_DAC_READ_SEARCH
  is the narrow fit (read + directory traverse only, not write) and
  is strictly narrower than the CAP_SYS_ADMIN already in the unit,
  so it does not expand the worst-case attack surface.
- Existing webmon installs running this version will pick up the new
  cap on the next `systemctl daemon-reload && systemctl restart
  smartmet-webmon` (RPM upgrade does not auto-restart the unit).

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-4.fmi
- Browser-flame zoom is now stable across the periodic refresh.
  flame.js's setData() used to unconditionally reset this.zoomPath
  to [] every cycle, popping the operator out of any zoom they had
  clicked into within seconds. Same conceptual bug the smtop TUI
  fix in 26.5.4-1 addressed Python-side; the browser path was
  missed. Now setData preserves zoomPath as user intent and draw()
  walks a *local* render path up just far enough to find a
  non-empty subtree when a deep leaf is missing from the latest
  refresh — the view springs back to the operator's zoom as soon
  as that leaf reappears.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-3.fmi
- Co-bumped with smartmet-monitor 26.5.4-3 to keep the
  Requires: smartmet-monitor = %{version}-%{release} pin
  satisfiable. No webmon-specific changes; the kernel-devel
  detection logic and %post warning live in smartmet-monitor.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-2.fmi
- /usr/lib/sysctl.d/99-smartmet-perf.conf moved to smartmet-monitor
  and is now shipped with kernel.perf_event_paranoid commented out;
  webmon no longer ships its own copy or runs `sysctl --system` in
  %post. Lowering paranoid is now an explicit operator action — the
  setting belongs to the host's hardening baseline owner. webmon
  still loads the kheaders module so bcc-tools can run as a non-root
  daemon. %description updated; full reasoning in
  /usr/share/doc/smartmet-monitor/perf-event-paranoid.md.
- Co-bumped with smartmet-monitor to 26.5.4-2 to keep the
  Requires: smartmet-monitor = %{version}-%{release} pin satisfiable.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-13.fmi
- New cluster Proc panel: per-backend memory (RSS), IO read /
  write rates, thread counts, and major page-fault rates as
  multi-line overlays — one line per backend, color-hashed the
  same way as every other cluster panel. The admin plugin does
  not serve /proc data, so the architecture is to fan out across
  each backend's *own* smwebmon: when a cluster's clusters.conf
  has a ``webmon-url-pattern`` set, the cluster discovery loop
  probes each backend's smwebmon at /api/health on every cycle,
  and the Proc panel calls the Proc-capable backends'
  /api/proc/detail in parallel at refresh time.
- New ``BackendInfo.webmon_ok`` flag tracked per backend; the
  cluster discovery_status string now reports e.g.
  ``ok (5/6 alive, 4 with smwebmon)`` so the operator can see
  at a glance how many backends are wired up for the cluster
  Proc panel.
- New cluster-scope endpoint /api/cluster/proc/detail returns
  ``{configured, backends: {prefix: {latest, series}}, errors}``.
  Single-host Proc panel unchanged: the panel branches on the
  active-cluster state, and renders the existing per-PID detail
  view when no cluster is selected.
- README "Cluster Proc panel" subsection documents the
  three-step setup (install smwebmon on each backend, bind to
  routable address, add webmon-url-pattern), with explicit
  security note about smwebmon being unauthenticated and the
  expectation of firewall-level restriction. clusters.conf
  template gets the optional key alongside admin-url-pattern.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-12.fmi
- Collapsed cards no longer leave dead space in the grid. The
  click-to-collapse from -10 hid only the card body; the card
  itself kept its grid cell, so adjacent cards could not reflow
  into the freed space. Now a collapsed card is fully removed
  from layout (``display: none`` on the whole card), and CSS
  Grid's ``auto-fit, minmax(420px, 1fr)`` automatically widens
  the remaining cards to fill the freed columns.
- A "hidden:" chip strip appears at the top of any panel with
  collapsed cards, listing each one as a clickable pill (e.g.
  ``hidden: [Memory ▸] [Page-faults ▸]``). Clicking a chip
  restores that card to the grid and removes the chip. The
  strip is empty when nothing is hidden, in which case CSS
  ``:empty`` collapses it so the panel keeps its full chrome.
- Each hidden card's title is persisted in localStorage
  alongside the ``collapsed: true`` flag, so the chip strip
  re-renders correctly across page reloads even before the
  panel's first refresh has built the cards.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-11.fmi
- Co-bumped with smartmet-monitor for the smtop curses-side section
  toggle work: bracketed ``[k]`` chips on every section divider,
  per-section toggle keys on Network (t/c/l/b) and Proc (m/i/g),
  and the Proc paired-cycle widget ``< b PID n >``. See
  smartmet-monitor changelog.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-10.fmi
- Click any card heading to collapse / expand it. Every
  ``.section-card`` in every panel becomes collapsible — the
  Network panel's TCP / Connection-states / Listen-sockets /
  Per-NIC bandwidth cards, the Proc panel's Memory / I/O /
  Threads / Page-fault cards, the Overview panel's history
  mini-charts, and the cluster-mode trend cards on Caches /
  Services / Plugins / Keys. A ``▾`` chevron rotates to ``▸``
  when collapsed; everything except the heading row hides
  (cluster trend cards keep the picker dropdowns visible too,
  via ``.panel-controls`` exemption). State persists in
  localStorage keyed by panel + slugified card title, so the
  operator's layout survives page reloads, tab switches, and
  the per-2-s panel refresh that rebuilds DOM in Network /
  Proc / modal-detail.
- Per-card vertical resize was deliberately deferred. Native
  ``resize: vertical`` works in the browser but it fights with
  the canvas-redraw cycle in panels that rebuild HTML on each
  refresh, and operators have not asked for height control
  beyond the existing per-canvas defaults. If they do, a
  ResizeObserver + targeted-canvas-redraw approach is the
  natural extension; the localStorage state shape already
  reserves a per-card ``height`` field so a future commit can
  add it without a schema bump.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-9.fmi
- Hover tooltip now works on every chart in the dashboard, not
  only the chrome-equipped drawLine/drawLineMulti charts.
  ``drawSparkline`` was previously chromeless on purpose (no axis
  ticks, no padding — it lives in tight per-row table cells), and
  in service of that purity it had no hover handler. Operators
  reported tooltips missing on Plugins / Services / Caches per-row
  trend sparklines and on the Network Connection-states per-state
  mini-charts. Now those sparklines wire the same pinned-Y tooltip
  the line charts use, with no chart redraw and no vertical guide
  (the cell is too small for that chrome) — just a value-and-time
  readout on hover. The tooltip's Y stays anchored to the canvas
  top edge, same anti-bounce rule as before.
- Per-row call sites pass the right ``fmtY`` so the tooltip renders
  values in their natural unit (latency in ms via formatMs, bytes
  via formatBytes, request rates as integers, hits/min with one
  decimal, connection counts as integers).

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-8.fmi
- Co-bumped with smartmet-monitor for the smtop hotkey case
  convention (uppercase = panel switch, lowercase = within-panel)
  + Proc panel ``n``-cycle restoration. See smartmet-monitor
  changelog.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-7.fmi
- Co-bumped with smartmet-monitor for the smtop Proc panel n/N
  fix (n now correctly switches to the Network panel; PID picker
  uses the per-row [1]/[2]/... red mnemonics instead). See
  smartmet-monitor changelog.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-6.fmi
- Default --journal-unit changed from "smartmet-server" to
  "smartmet-backend,smartmet-frontend". The previous default was
  factually wrong: SmartMet's systemd units are named smartmet-backend
  (data daemon) and smartmet-frontend (Sputnik routing daemon), and
  both can coexist on the same physical host. The CLI now accepts
  a comma-separated list and spawns a single journalctl with
  multiple -u flags so the Logs panel's [journal] source carries
  all listed units' output in one timestamp-merged stream — covers
  a host running either or both daemons without operator
  intervention. Pass an empty string to disable; pass any single
  unit name to opt back into the old single-unit behavior.
- Co-bumped with smartmet-monitor.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-5.fmi
- Bump smartmet_top.__version__ and smartmet_webmon.__version__ to
  26.5.2 (the spec versions had been bumped over the cluster-view
  Phase 2/3 work, but the Python package metadata still reported
  26.4.30 at runtime). The Makefile's source-tarball name is
  derived from __version__, so this also fixes the make-rpms
  failure where rpmbuild looked for smartmet-monitor-26.5.2.tar.gz
  while git archive was producing smartmet-monitor-26.4.30.tar.gz.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-4.fmi
- Cluster on-demand lastrequests fetches now share a 10 s server-side
  cache keyed on (cluster, minutes). Without it, the URL drill-down
  modal's 2 s panel refresh fired N parallel admin-plugin fetches at
  minutes=60 every refresh — for a 6-backend cluster that was 180
  large lastrequests calls per minute per cluster. With the cache,
  the first chart refresh after a TTL window does the parallel fetch
  and everything within the window (the modal's 2 s tick, multiple
  chart endpoints serving the same panel — URLs / Plugins / Keys /
  Overview all default to minutes=60 so they share one fetch) reuses
  the result. Backend admin-plugin load drops by ~5×; the chart
  still feels live with ~10 s update granularity. The TTL is short
  enough that a backend coming back online appears in the chart
  within one cycle without operator intervention.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-3.fmi
- Cluster multi-line charts no longer draw a misleading flat-zero
  line for backends whose lastrequests fetch failed. The errored
  backends are now omitted from the chart series; they appear in
  the legend with a ⚠ marker, color-hashed to their normal color
  but drawn at 40 % opacity, with the failure reason as a tooltip.
  Previously a "fetch failed" backend was indistinguishable from
  a "no traffic for this URL/plugin/key" backend on the chart —
  both rendered as a flat line at zero. Now the legend tells the
  operator the truth.
- Refactored the four panel legend builders (URLs / Plugins /
  Keys / Active) to share a single _buildClusterLegend helper.
  Cuts ~80 lines of duplicated DOM-building. The errored-prefix
  pass at the end ensures the legend lists every prefix the
  cluster polled, not only the ones the chart shows.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-2.fmi
- Cluster-view Phase 3: backend-pill topology strip below the top
  bar (cluster mode only). One pill per backend prefix; the dot
  inside each pill is the same color the chart legends use, so
  identifying which backend a line belongs to is a single glance.
  Hover surfaces the backend's handler list (truncated at 40
  entries with a "…and N more" marker for grid-content-heavy
  prefixes like q3 satellites). A backend that is registered
  but has no handlers in clusterinfo (offline / draining /
  paused) renders muted with a strikethrough.
- Topology refreshes every 30 s (same cadence as the cluster
  selector dropdown) but is debounced on a content hash, so the
  operator's mid-hover position is not lost on idle refreshes.
- README documents cluster mode end-to-end: topology strip
  reading guide (healthy shape / trouble pattern / typical root
  cause / where to look next), per-panel data-path table
  (which panels reuse the 2 s polling vs which fire on-demand
  parallel lastrequests fetches at chart-refresh time), and
  the multi-line chart reading guide.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-1.fmi
- Cluster-view Phase 2c (Plugins / Keys) and Phase 2d (Overview): the
  remaining cluster-mode multi-line trend charts. With this commit
  every panel that produces a time-series chart in single-host mode
  now has a per-backend overlay equivalent in cluster mode.
- Plugins panel: a "per-backend plugin trend" card on top of the
  table. Pick a plugin (the leading URL path segment, derived from
  the most recent admin lastrequests fetch) and metric, see one line
  per backend. Same color hashing and clickable-legend pattern as
  the URLs/Active/Caches/Services panels.
- Keys panel: same shape, picker is the apikey (excluding the dash
  placeholder). Hover tooltip lists every backend's value at the
  cursor, sorted descending, just like the other multi-line charts.
- Overview panel: each of the five mini-charts (req/min, mean ms,
  p95 ms, bytes/min, err %) becomes a multi-line per-backend overlay
  in cluster mode, with one parallel HTTP fetch producing all five
  metrics — N backend admin calls per panel refresh, not 5N. The
  ``metrics=`` query of /api/cluster/overview/chart accepts a
  comma-separated list and returns ``charts: {metric: ...}``.
  bytes/err_pct fall back to the existing single-line endpoint
  because lastrequests rows do not retain bytes/status — a future
  refactor of _aggregate_minute could lift this if operators ask
  for per-backend bytes specifically.
- handlers.py refactor: extracted _resolve_cluster,
  _fetch_cluster_lastreqs and _build_cluster_chart so the four
  on-demand cluster chart endpoints (URLs, Plugins, Keys, Overview)
  share one parallel-fetch + bucket-and-aggregate pipeline. Each
  endpoint is now a thin wrapper specifying its own row_matches
  filter.
- Day rolled past midnight → Version bump from 26.4.30-N to
  26.5.2-1 per the YY.M.D scheme.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-17.fmi
- Cluster-view Phase 2b: Caches and Services panels add a per-backend
  trend chart in cluster mode. Pick a cache (or service handler) from
  the dropdown and the multi-line chart shows that entity's metric
  over time, one line per backend, same color hashing as the URLs
  and Active panels. Metric pickers cover hits/min, inserts/min,
  hit %, size for caches; req/min, req/hour, req/day, avg ms,
  avg cpu ms for services. Clickable legend toggles per-backend
  visibility.
- Data path is zero extra HTTP: cachestats and servicestats are
  already polled per-host on the 2 s admin cadence (one task per
  backend, asyncio.gather in adminapi.poll_all), and the per-host
  results land in store.cache_history and store.service_history.
  The new cluster_chart_per_host snapshot methods just rearrange
  the existing per-host series into the {label, values} shape that
  drawLineMulti consumes. Cluster size scales linearly in storage
  cost only (no extra requests per panel refresh).
- New endpoints /api/caches/cluster_chart and
  /api/services/cluster_chart. Both return the per-host series
  plus the union of available entity names so the UI's dropdown
  stays current as new backends come online (the cluster's
  discovery loop catches added prefixes within ~60 s).
- Single-host mode unchanged: the trend card stays hidden and the
  per-row sparkline-trend column continues to be the operator's
  view. Cluster mode shows the chart card above the table; the
  per-row trends remain so the table is still useful for
  cross-backend at-a-glance scanning.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-16.fmi
- Chart hover tooltip no longer bounces vertically. The tooltip box
  is now pinned to the canvas's top edge in viewport coordinates and
  only its X tracks the cursor (with edge-flip when there is no room
  on the right). Earlier the box followed e.clientY, so as the
  operator's cursor naturally tracked peaks and valleys in a busy
  latency chart the tooltip jittered up and down — distracting and
  hard to read. The vertical guide line and per-series dots still
  appear AT the cursor's data points; only the value-readout box
  is anchored.
- The tooltip is now multi-row for drawLineMulti charts: one row
  per backend with a color swatch, label, and value, sorted
  descending so the busiest backend is at the top. (Previously
  even cluster-mode charts showed only a single value.)

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-15.fmi
- Cluster-view Phase 2c: URLs panel drill-down chart shows one line
  per backend in cluster mode. Click a row in the URLs table; the
  modal's "Per-backend latency, last 60 min" chart renders an
  overlay with one line per alive backend (same color hashing as
  the Active panel, so c2 matches across panels). A metric picker
  in the chart header switches between p95 / p50 / mean / max /
  count. Clicking a legend entry hides that backend's line.
- Data path is on-demand parallel: when the chart refreshes, the
  cluster-scope handler fires one HTTP request per backend (a
  cluster has ≤10 backends in practice) to /admin?what=lastrequests
  &minutes=60. ThreadPoolExecutor with one worker per backend means
  wall time ≈ slowest backend, not sum. The rows are bucketed by
  minute on the operator-clicked URL and the chosen metric is
  computed per minute. No changes to the existing 2 s admin
  polling — the per-cluster store still gets fed by it for the
  URL table; the chart just reaches around the store to get
  per-host attribution that the store does not retain.
- New endpoint /api/cluster/urls/chart (cluster-scope) that
  returns {series: [{label: prefix, values: [...]}], errors:
  {prefix: msg}}. Errors per backend are surfaced inline in the
  legend (a ⚠ next to the backend name with the error reason as
  a tooltip) so a single misbehaving backend doesn't fail the
  whole chart. Single-host mode keeps using /api/urls/chart as
  before; the modal picks endpoint based on cluster mode.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-14.fmi
- Cluster-view Phase 2a: Active panel reshaped for cluster mode.
  When a cluster is selected, the in-flight count chart shows one
  line per backend instead of the aggregated cluster-total. Each
  line gets a stable color (Tableau-categorical 10-slot palette
  hashed by backend prefix, so c2 is the same color across every
  panel and every refresh). Clickable legend below the chart
  toggles per-backend visibility. Single-host mode (no cluster
  selected) keeps the existing aggregated single-line shape
  unchanged.
- New chart helper drawLineMulti in chart.js — accepts
  [{label, color, values}, ...] and renders all overlaid with
  shared Y-axis nice-ticks (Heckbert), shared X-axis time labels,
  and a hover crosshair that draws a dot per series at the
  cursor's index plus a vertical guide line. Existing drawLine
  unchanged; the multi variant is a peer.
- New ActiveSnapshot.chart_per_host returning per-backend
  in-flight count series. Existing chart() (aggregated total)
  unchanged; the per-host variant is what the cluster-mode UI
  fetches via ?multi=1 on the existing /api/active/chart endpoint.
  Backwards-compatible: old clients without ?multi=1 still get
  the aggregated form.
- Color assignment is deterministic (hash of label → palette slot)
  so operators learn one mapping cluster-wide instead of having
  to re-orient on every panel.

  Phase 2 still has: URLs / Plugins / Caches / Services / Keys
  to reshape (separate commits — Caches and Services already have
  per-host data in Store and are next; URLs / Plugins / Keys need
  per-host accumulation in Store first because their stats are
  currently global tail-derived).

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-13.fmi
- Auto-detect cluster naming changed from FQDN-based to prefix-
  family-based. The FQDN approach (split <prefix>.<cluster>.<rest>
  and use <cluster> as the name) misidentified two of the three FMI
  clusters: `in1.back.smartmet.fmi.fi` and `c3.back.smartmet.fmi.fi`
  share the `back.smartmet.fmi.fi` domain, so the FQDN-derived
  name collided. `open1.smartmet.fmi.fi` had no `back` segment at
  all, so the FQDN-derived name landed on `smartmet`. The
  cluster identity is actually in the *prefix family* the local
  frontend routes to:
    * c1..c6 → cluster name "c" (FMI calls it back)
    * in1..in4 → cluster name "in" (FMI calls it internal)
    * open1..open3 → cluster name "open" (FMI calls it opendata)
  Auto-detect now derives the name by stripping trailing digits
  from each backend prefix and picking the most common stem.
  Specialised dotted prefixes (e.g. v1.q3, v2.q3 q3-engine
  pseudo-backends on the back cluster) are skipped during naming —
  they're satellites, not what defines the cluster identity. The
  `admin-url-pattern` still uses the local FQDN's tail as the
  cluster's DNS domain, so all backends in the cluster are
  reached as <other-prefix>.<this-domain>:8081 — that part of the
  heuristic was correct in -12 and stays.
- Operators who want friendlier names (`back` instead of `c`,
  `internal` instead of `in`, `opendata` instead of `open`)
  override via `/etc/smartmet-webmon/clusters.conf`. The
  auto-detect output is "honest about what's observable" rather
  than embedding FMI-specific naming knowledge in the package.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-12.fmi
- Cluster-view groundwork (Phase 1 of 3). The dashboard can now
  monitor multiple SmartMet clusters from a single smwebmon
  instance: cluster discovery via the frontend's clusterinfo HTML,
  per-backend admin polling through cluster-specific URL patterns,
  cluster selector dropdown in the top bar, ?cluster=NAME on every
  /api/* endpoint to route to the right per-cluster Store.
- Auto-detection: when no clusters.conf is present (or empty),
  smwebmon probes localhost for a SmartMet daemon, parses its
  clusterinfo to identify role (FRONTEND vs BACKEND), and on a
  frontend host derives a cluster definition from the local FQDN
  (`<prefix>.<cluster>.<domain>` → cluster name = `<cluster>`,
  admin-url-pattern = `http://{prefix}.<cluster>.<domain>:8081/admin`).
  On a backend host or any FQDN that doesn't match the convention,
  auto-detect quietly returns and single-host mode (existing 26.4
  behaviour) takes over. --no-cluster-autodetect opts out.
- /etc/smartmet-webmon/clusters.conf — INI-style file with one
  section per cluster (frontend-url, admin-url-pattern, optional
  log-glob / admin-interval / discovery-interval). Shipped with all
  cluster sections commented out, so a fresh install lands in
  auto-detect mode. Operators with non-FMI naming conventions
  uncomment + adjust. %config(noreplace), so site edits survive
  upgrades.
- Two new endpoints exposed for the dashboard:
    /api/clusters             — list configured clusters with
                                discovery status (alive/total
                                backend counts).
    /api/cluster/topology     — per-cluster backend list with
                                handler service mix; powers the
                                planned topology card.
- The same smartmet-webmon RPM works on backend and frontend
  hosts. Behaviour is configuration-driven: backend host =
  single-host mode (existing perf samplers + admin polling
  localhost); frontend host = cluster mode (admin polling N
  backends through the frontend's local clusterinfo). Both modes
  can co-exist when frontend is colocated with a backend's
  smartmetd.
- Existing single-host functionality unchanged. With no
  clusters.conf and auto-detect off, smwebmon behaves exactly as
  in -11.

  Phase 2 (multi-line cluster panels — overlay one line per
  backend per panel) and Phase 3 (cluster topology card +
  aggregates) are queued; this release is the wiring layer.
  Existing per-host panels keep rendering all backends as separate
  rows when ?cluster= is in scope, which is functional but not
  the eventual cluster-friendly shape.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-11.fmi
- Chart Y-axis nice-tick algorithm switched from qdstat-style
  (2/5/10 ladder) to Heckbert-style (1/2/5/10 ladder). The two
  algorithms agree on most inputs, but differ when vmax falls
  in the "needs a 1-step" range. Concrete examples where qdstat
  would produce coarser labels than the operator wants:
                            qdstat            Heckbert
    vmax=5,  maxTicks=5      0, 2, 4, 6        0, 1, 2, 3, 4, 5
    vmax=50, maxTicks=5      0, 20, 40, 60     0, 10, 20, 30, 40, 50
    vmax=6,  maxTicks=4      0, 2, 4, 6        0, 5, 10        (sparser, but right ladder)
  Why: qdstat's 2/5/10 ladder is the right thing for histogram
  *bin* boundaries — operators of qdstat want "double / halve the
  bin count" granularity which lands naturally on 2/5/10. Chart
  *axis* labels are a different problem; an operator looking at a
  monitor chart that peaks at 5 reads 0, 1, 2, 3, 4, 5 more
  naturally than 0, 2, 4, 6 with the axis overshooting to 6 to
  contain the data. The 1 multiplier fills the gap. The 1/2/5/10
  ladder is also the de-facto standard across chart libraries
  (matplotlib, d3, plotly, R's pretty(), ggplot2), matching what
  operators have learned elsewhere. qdstat itself stays unchanged
  — its 2/5/10 ladder is correct for its histogram-bin use case;
  smwebmon and qdstat now use different algorithms because they
  serve different audiences. Inline comment in chart.js spells
  out the reasoning so the next person to look at it doesn't
  re-port qdstat's algorithm again "for consistency."

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-10.fmi
- Y-axis labels on every chart now use "nice" tick boundaries
  (multiples of 1, 2, or 5 times a power of 10) instead of raw
  vmax values. JS port of qdtools/main/qdstat.cpp:autotick +
  autoscale (the FMI house algorithm for histogram bin
  boundaries), so qdstat and smwebmon round the same way. The
  user reported axis labels like 0, 0.81, 1.62, 2.42, 3.23 —
  those become 0, 1, 2, 3, 4. For larger ranges: 0, 2, 4, 6, 8
  or 0, 5, 10, 15 as the data demands. Tick count adapts to
  chart height (more ticks on tall charts, fewer on the small
  ones in the Proc / Network grids). Subtle horizontal grid
  lines at each interior tick.
- Y-axis chart scale now uses niceMax instead of dataMax, so
  the topmost tick aligns with the chart's top edge instead of
  the line just barely touching the ceiling.
- Format helpers (formatMs / formatBytes / formatCount) trim
  trailing-zero fractions so a nice-tick value of 1 renders as
  "1ms" not "1.0ms" and 4 as "4" not "4.0".

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-9.fmi
- Co-bumped with smartmet-monitor for the bulk_load-blocks-the-
  loop fix. The 26.4.30-8 reorder (schedule samplers before
  awaiting replay) was a necessary but insufficient half of the
  fix; this release adds the other half. After upgrading, the
  Flame panel populates with samples from second one even while
  --replay is in progress. See smartmet-monitor changelog for
  details.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-8.fmi
- The unit now ships with AmbientCapabilities=CAP_SYS_PTRACE +
  CAP_SYS_ADMIN and NoNewPrivileges=no. Investigation on
  c3.back.smartmet.fmi.fi showed perf_event_open(2) was failing
  with EACCES even at kernel.perf_event_paranoid=-1 + SELinux
  Permissive + lockdown=none + same-uid as smartmetd (i.e. every
  obvious wall removed). Root cause: smartmetd is launched by the
  smartmet-server unit with NoNewPrivileges=1, which sets the
  process's dumpable flag to 0; the kernel's ptrace_may_access()
  check then requires the caller to hold CAP_SYS_PTRACE — same-
  uid does NOT bypass this and no perf_event_paranoid level
  bypasses it. CAP_SYS_PTRACE in our unit's AmbientCapabilities
  satisfies the check. CAP_SYS_ADMIN unlocks the wakeup /
  blockflame raw-tracepoint events at paranoid=0; CAP_PERFMON
  would be the narrower fit on kernel >= 5.8 but RHEL 8's 4.18
  doesn't expose it. NoNewPrivileges had to come off because
  AmbientCapabilities doesn't take effect with it set; the
  remaining hardening (ProtectSystem=strict, ProtectHome,
  PrivateTmp) stays.
- Recommends: kernel-devel-uname-r so the bcc-tools modes
  (off-CPU / biolat / runqlat) work without a separate operator
  step. Their failure mode was a chdir into
  /lib/modules/$(uname -r)/build/ which only exists when
  kernel-devel is installed. The kheaders module loaded at boot
  is a separate header source that bcc on RHEL 8 doesn't yet
  consume, so kheaders alone wasn't enough.
- smwebmon now schedules sampler tasks BEFORE awaiting --replay.
  asyncio runs them in parallel with the replay's blocking await,
  so /api/flame/status reports real sampler state from second
  one instead of "(disabled)" for the duration of the replay
  scan (which can take minutes on a busy backend with default
  --history-minutes=1440 and 1 GiB --replay-bytes). Stderr also
  now logs "replay starting / done in Ns / scheduled N source
  tasks" so the journal shows what stage startup is in.
- Web UI: per-chart mouse-over with vertical guide line, dot at
  the cursor's data point, and a floating tooltip showing
  HH:MM:SS plus the formatted value. The X-axis grew from two
  static labels to 3-5 evenly-spaced HH:MM ticks. Hover overlay
  survives 2-second poll repaints. Wired up for the Overview
  charts, the Active in-flight chart, the URLs detail-modal
  60-min chart, and the Proc panel's RSS / IO / threads /
  majflt charts. (Network panel charts still need timestamp
  metadata in the snapshot — deferred to a follow-up.)
- Web UI: Caches and Services panels' name column widened so
  long cache names / handler names display fully instead of
  collapsing to a few characters. The trailing trend sparkline
  now compresses to its 80 px minimum when the name needs the
  width, instead of greedily claiming 100 % of the row.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-7.fmi
- The package now ships the kernel-side prereqs the dashboard needs
  to be useful: kernel.perf_event_paranoid = 0 via
  /usr/lib/sysctl.d/99-smartmet-perf.conf and `kheaders` via
  /usr/lib/modules-load.d/smartmet-perf.conf, both applied at
  install time without requiring a reboot (sysctl --system + modprobe
  kheaders in %post, with || : guards). Without these the Flame panel
  is reduced to "no samples" and the Proc panel's perfstat numbers
  show as zero — every operator who installed -1 through -6 hit this.
  The package's audience is operators who run the dashboard as a
  deliberate decision; sites that can't accept paranoid=0 should
  use the smartmet-monitor CLI tools (smtop, bstat, bperf) instead
  and skip this package.
- %description rewritten to make these install-time host changes
  explicit and to point at perf-event-paranoid.md for the full
  reasoning.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-6.fmi
- Flame tab now shows the full multi-line perf stderr (and the
  per-mode status for off-CPU / page-fault / wakeup / blockflame /
  malloc / biolat / runqlat / perfstat) when a sampler fails. The
  panel header keeps showing the truncated one-line summary so it
  stays compact; below the breadcrumb a new "Sampler diagnostics"
  card surfaces every failing mode's full status text in monospace.
  The "Error:" first-line truncation is no longer the operator's
  only window into a perf failure — they see exactly what perf is
  complaining about. See smartmet-monitor changelog for the
  underlying snapshot change.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-5.fmi
- Default unit user changed from `smartmet` to `smartmet-server`.
  This is the user that owns smartmetd processes in production
  FMI deployments, and the kernel sysctl
  kernel.perf_event_paranoid=2 (the RHEL default) only allows
  perf record for processes the calling user owns. Profiling
  cross-uid was failing with exit=255 and a near-empty diagnostic
  even though paranoid=2 should "permit profiling" — the catch is
  the "your own processes" constraint. The unit now runs as the
  same user as smartmetd, so perf works out of the box. Group is
  intentionally unset so systemd derives it from the user's
  primary group in /etc/passwd (htj at FMI, smartmet-server
  elsewhere); use a drop-in if your site needs something different.
- The %pre useradd that created a `smartmet` user has been
  dropped. That user was a miscalibration in the original spec
  (-1 was based on an off-the-cuff name; production uses
  smartmet-server). Recommends: smartmet-server declares the
  actual dependency without making it a hard requirement (some
  sites build smartmet-server from source under different names).
- Requires(pre): shadow-utils dropped — no scriptlet uses useradd
  any more.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-4.fmi
- flame.js was not being shipped in the RPM payload. Same install
  bug class as the snapshots/ fix in -3: WEBMON_ASSETS was a
  hand-maintained list and flame.js (added 26.4.28-2) had not been
  added to it. The browser fetched /static/flame.js, got 404,
  FlameView never loaded, and the Flame tab failed with
  "flame is undefined". The install-webmon target now auto-discovers
  files in share/smartmet/webmon/ instead — same shape as the
  smartmet_top subpackage discovery so the same bug class can't
  recur.
- The Flame-tab failure now surfaces the cause instead of the
  symptom: activatePanel() catches init errors, marks the panel
  inactive (so the polling loop stops re-throwing every 2 s), and
  shows a panel-empty card with the actual error and a hint to
  hard-refresh the cached static assets.
- `--replay` now defaults ON for smwebmon (URLs panel comes up
  populated from log history at startup). Pass --no-replay to opt
  out — the sysconfig template documents both directions.
- `--perf` now defaults ON for smwebmon, so the Flame tab works
  out of the box. Same scope as smtop --perf: on-CPU, off-CPU,
  page-fault, wakeup, block-I/O, plus biolat / runqlat / perfstat.
  --no-perf opts out. New flags --perf-interval, --perf-record-seconds
  and --malloc-flame mirror smtop. Requires perf installed and
  kernel.perf_event_paranoid <= 2 (the RHEL default).
- Unit's CPUQuota raised from 50% to 200% so perf record /
  offcputime-bpfcc burst windows fit comfortably under the cgroup
  ceiling. MemoryMax stays at 512M. Both can be raised in a drop-in
  if a hot box wants more.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-3.fmi
- Co-bumped with smartmet-monitor for the install-rule fix that
  was causing smwebmon to fail-fast at startup with
  `ModuleNotFoundError: No module named 'smartmet_top.snapshots'`.
  See smartmet-monitor changelog for details. Once both -3 RPMs
  are installed, `systemctl start smartmet-webmon` works.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-2.fmi
- Co-bumped with smartmet-monitor for the make-check fix that
  restores the RPM build on the RHEL 8 build host (HTTP proxy
  was intercepting the loopback test requests). See
  smartmet-monitor changelog for details.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-1.fmi
- Spec file lists the smwebmon(1) man page in %files. The
  install-webmon Makefile target was already shipping it, but the
  spec missed it, so `make rpms` failed at the unpackaged-files
  check.
- Co-bumped with smartmet-monitor for the build-system fix that
  allows multi-spec tarballs to build via `rpmbuild -bb` instead
  of `rpmbuild -tb`. See smartmet-monitor changelog for details.

* Tue Apr 28 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.28-3.fmi
- smwebmon auto-probes localhost:8080 (frontend) and localhost:8081
  (backend) at startup when no -u is given, removing the need to
  configure /etc/sysconfig/smartmet-webmon at all on a typical
  SmartMet host. The unit can now be started directly after
  install with no edits. Pass --no-admin to disable.
- Sysconfig template rewritten to reflect the auto-probe default;
  shows when overriding -u is actually needed (remote hosts or
  non-standard ports) rather than presenting it as required.

* Tue Apr 28 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.28-2.fmi
- Full panel parity with smtop. Tab navigation across every
  smtop panel (Overview, Plugins, URLs, Caches, Services, Active,
  Keys, Proc, Network, Flame, Logs); each panel renders the same
  data smtop shows but with HTML Canvas charts replacing Braille
  sparklines, click-to-drill replacing keystroke navigation,
  and bookmarkable per-panel URLs (`/#/<panel>`). Highlights:
    * Overview: 5 full-width Canvas line charts over the retained
      history (req/min, mean ms, p95 ms, bytes/min, err %).
    * Plugins: per-row latency + size sparklines, sortable
      by req/s / mean / p95 / err / bytes; window 60s..60m.
    * Caches: per-row hit-rate fill bar (color thresholds) plus
      hits/min trend sparkline; size cell coloured by fill ratio.
    * Services: per-row req/min sparkline; cpu% column coloured
      green / blue / neutral by ratio (parity with the curses
      column added in -13 / -14).
    * Active: top in-flight count Canvas line chart + sortable
      table of currently-active requests.
    * Keys: window/sort/filter controls plus drill-down modal
      showing per-window stats and the top URLs hit by that key.
    * Proc: PID picker plus a memory / IO / threads+fds /
      page-fault grid, each section with a per-PID Canvas line
      chart.
    * Network: TCP summary (retrans/s + listen overflow/drop/s
      with line chart), connection states with per-state trend
      sparklines, listen sockets with recv-Q (highlighted when
      non-zero), per-NIC rx + tx Canvas charts.
    * Flame: interactive Canvas flame graph with mouse zoom
      (click rectangle → that frame becomes the new root),
      click-the-breadcrumb to zoom out, hover tooltip with full
      function name + weight + percentage, search box that
      highlights matching frames and greys non-matches,
      deterministic per-name coloring (yellows/oranges for
      SmartMet:: frames so they pop against blue/violet glibc
      and kernel frames). Mode bar (on-cpu, off-cpu,
      off-cpu-locks, pagefault, wakeup, blockflame, malloc),
      thread-class bar (all / request / background) and a
      smartmet-only toggle compose with the existing curses
      filter logic.
    * Logs: live tail of the multi-source log ring with
      substring filter and autoscroll toggle.
- 24 JSON endpoints under /api/* (one per panel + chart/detail
  variants) plus /api/panels for client-side tab discovery.
  Every endpoint smoke-tested in `make check`.

* Tue Apr 28 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.28-1.fmi
- Initial release. Browser dashboard companion to smartmet-monitor.
  Ships `smwebmon` (HTTP+JSON server) plus static assets; reuses the
  data-collection layer (Store, sources, snapshots) from
  smartmet-monitor at the exact-version level. URLs panel only in
  v1, with click-to-drill-down: per-window stats, latency histogram
  (HTML Canvas), status-code mix, top API keys, last-60-min mean
  latency line chart. systemd unit shipped disabled — start with
  `sudo systemctl start smartmet-webmon` when needed and SSH-tunnel
  to 127.0.0.1:8765. Runs as user `smartmet` so it reads the same
  access logs the daemon writes. No X11, no third-party Python deps.
