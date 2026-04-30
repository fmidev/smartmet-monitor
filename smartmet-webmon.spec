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
Version:        26.4.30
Release:        8%{?dist}.fmi
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
Installing it makes two host-level changes the dashboard needs to
work fully:

  * /usr/lib/sysctl.d/99-smartmet-perf.conf sets
    kernel.perf_event_paranoid = 0 (the RHEL default 2 denies
    hardware perf counters and tracepoints to unprivileged users,
    breaking the Flame panel and the Proc panel's perfstat numbers).
  * /usr/lib/modules-load.d/smartmet-perf.conf pre-loads the
    kheaders kernel module so bcc-tools (offcputime-bpfcc,
    biolatency-bpfcc, runqlat-bpfcc) can run without root.

Both are applied at install time without requiring a reboot. The
full reasoning, per-feature compatibility table, and security
trade-offs are in
/usr/share/doc/smartmet-monitor/perf-event-paranoid.md. Sites that
can't accept those defaults should use the CLI tools (smtop, bstat,
bperf) from smartmet-monitor instead and not install this package.

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
# Apply the perf-event sysctl and load the kheaders module without
# requiring a reboot. Both are also persistent across reboots via
# the files in /usr/lib/sysctl.d/ and /usr/lib/modules-load.d/. The
# || : guards keep package install from failing on hosts where the
# kernel doesn't expose these (e.g. very old kernels that lack the
# kheaders module); the dashboard will surface the resulting per-
# sampler errors via /api/flame/status.
sysctl --system >/dev/null 2>&1 || :
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
%{_prefix}/lib/sysctl.d/99-smartmet-perf.conf
%{_prefix}/lib/modules-load.d/smartmet-perf.conf
%{_python3_sitelib}/smartmet_webmon/
%{_mandir}/man1/smwebmon.1*

%changelog
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
