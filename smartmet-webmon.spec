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
Version:        26.4.28
Release:        3%{?dist}.fmi
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

# The unit runs as user `smartmet`, mirroring the SmartMet daemon's
# user so the dashboard reads the same access logs the daemon writes.
# The smartmet user is normally created by smartmet-server's RPM; we
# create it here too so smartmet-webmon can be installed standalone
# (idempotent — does nothing if the user already exists).
Requires(pre):  shadow-utils

%description
Browser-based companion to smartmet-monitor. Adds the `smwebmon`
daemon that serves a small dashboard (URLs panel in v1, more to
follow) over HTTP+JSON on loopback. Reuses the data-collection layer
from smartmet-monitor; does not pull X11 or any third-party Python
packages.

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

%pre
getent passwd smartmet >/dev/null || \
    useradd -r -s /sbin/nologin -d /var/lib/smartmet \
            -c "SmartMet Server" smartmet

%post
%systemd_post smartmet-webmon.service

%preun
%systemd_preun smartmet-webmon.service

%postun
%systemd_postun_with_restart smartmet-webmon.service

%files
%{_bindir}/smwebmon
%{_datadir}/smartmet/webmon/
%{_unitdir}/smartmet-webmon.service
%config(noreplace) %{_sysconfdir}/sysconfig/smartmet-webmon
%{_python3_sitelib}/smartmet_webmon/

%changelog
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
