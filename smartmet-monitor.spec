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

# This package is built arch-specific (no `BuildArch: noarch`, see
# below) purely to route it into the per-distro repos, but it contains
# no compiled code. Suppress the empty debuginfo/debugsource
# subpackages rpm would otherwise try to generate for an arch build —
# without this the build fails with "Empty %%files file ...debugsourcefiles.list".
%global debug_package %{nil}

Name:           smartmet-monitor
Version:        26.6.25
Release:        1%{?dist}.fmi
Summary:        Log analysis and live monitoring tools for SmartMet Server
License:        MIT
URL:            https://github.com/fmidev/smartmet-monitor
Source0:        %{name}-%{version}.tar.gz
# NOT noarch — deliberately. The payload is arch-independent
# (pure-stdlib Python + Bash), but a noarch build lands in the single
# shared `smartmet-open-noarch` repo that every distro reads from, so an
# el8 build (which Requires python39 / python(abi) = 3.9) becomes a
# candidate on RHEL 10 and fails to install there. Building arch-
# specific routes each build into the existing per-distro x86_64 repos
# that operators already consume, so el8 and el10 builds never collide.
# Do not add `BuildArch: noarch` back without first splitting the noarch
# repo per distro.

BuildRequires:  python%{python3_pkgversion}
BuildRequires:  python%{python3_pkgversion}-rpm-macros
BuildRequires:  make
# The smartmet-webmon subpackage installs a systemd unit; the
# %%systemd_post / %%systemd_preun / %%systemd_postun_with_restart
# macros come from systemd-rpm-macros. (Escaped with %%%% so rpm
# does not expand the macros inside this comment.)
BuildRequires:  systemd-rpm-macros

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
# The Heap section in the Proc panel polls spine's
# ?what=mallocstats endpoint, which was added in
# smartmet-library-spine 26.4.27. Older spine builds will return
# 404 / "endpoint not found" and the panel renders an empty state;
# operators upgrading the monitor without upgrading spine will see
# the section but no data. The Recommends nudges the package
# manager to keep them aligned without making a full Requires.
Recommends:     smartmet-library-spine >= 26.4.27
# bpftrace is the scripting alternative used for futex / lock-wait
# stack traces. Optional; the off-CPU view falls back to bcc-tools
# alone when bpftrace is missing.
Recommends:     bpftrace
# bcc-tools (offcputime-bpfcc, biolat-bpfcc, runqlat-bpfcc) compile
# their BPF C source at runtime via libclang against the in-tree
# kernel headers at /lib/modules/$(uname -r)/build. They need
# kernel-devel matching the *running* kernel — not just any
# kernel-devel. An RPM has no way to express that constraint:
# `Requires: kernel-devel` resolves at install time on the latest
# version dnf can find, which is routinely newer than what's running
# (especially on hosts that haven't rebooted since their last kernel
# update). The earlier 26.5.4-2 build did Require kernel-devel and
# the resulting silent mismatch was worse than no Requires at all —
# the operator saw "kernel-devel installed" yet bcc-tools still
# failed with `chdir(.../build): No such file or directory`.
#
# kernel-devel-uname-r is a virtual provide each kernel-devel package
# carries; the Recommends keeps it on the operator's radar without
# claiming a guarantee we can't deliver. Runtime detection in
# profile_caps.kernel_build_dir() distinguishes "missing", "dangling
# symlink (wrong version installed)", and "valid", and the Flame
# panel's off-CPU mode surfaces a clean install hint with the exact
# `dnf install kernel-devel-$(uname -r)` command and the
# `modprobe kheaders` fallback.
Recommends:     kernel-devel-uname-r

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

The browser-based companion `smwebmon` is built from the same source
tree and shipped as a separate optional subpackage
(`smartmet-monitor-web`) so this RPM stays free of extra dependencies
for sites that only want the CLI tools. (The subpackage was named
`smartmet-webmon` through 26.5.19-1.fmi; it carries Obsoletes/Provides
on the old name so upgrades from any earlier release work without
operator intervention.)

Both parts are implemented with the Python 3 standard library; no pip
packages are required at runtime.


# smartmet-monitor-web — browser-dashboard companion to smartmet-monitor.
# (Previously distributed as the standalone `smartmet-webmon` package,
# then briefly as a same-named subpackage through 26.5.19-1.fmi.)
#
# Ships a single daemon (`smwebmon`) plus the static HTML/CSS/JS it
# serves over HTTP. The shared data-collection layer (sources,
# state.Store, snapshots) lives in smartmet-monitor and is depended
# on at the exact-version level.
#
# The unit is shipped DISABLED by default. The systemd unit name
# stays `smartmet-webmon.service` (and the sysconfig file at
# /etc/sysconfig/smartmet-webmon, and the config dir
# /etc/smartmet-webmon/) so operator muscle memory is preserved
# across the package rename; only the RPM identifier changes.
# Operators run `sudo systemctl start smartmet-webmon` only when
# they want the dashboard, then SSH-tunnel to localhost.
%package -n smartmet-monitor-web
Summary:        Browser dashboard for SmartMet Server (smwebmon)

# Upgrade path from the historical `smartmet-webmon` standalone
# package (and from the brief lifetime of the `smartmet-webmon`
# subpackage name, 26.5.5 → 26.5.19-1.fmi). Obsoletes pulls the
# old package out on upgrade; Provides keeps `Requires:
# smartmet-webmon` clauses elsewhere in the ecosystem satisfiable
# without forcing a coordinated rename. The systemd unit, sysconfig
# file, /etc/smartmet-webmon/ config dir, and the `smwebmon` binary
# itself keep their existing names — only the RPM identifier changes.
Obsoletes:      smartmet-webmon < %{version}-%{release}
Provides:       smartmet-webmon = %{version}-%{release}

# Exact-version dep — the dashboard imports smartmet_top.snapshots,
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

%description -n smartmet-monitor-web
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
not to a monitoring tool — the smartmet-monitor package ships an
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
%setup -q

%build
# Nothing to compile — Python stdlib only. The Makefile's check target
# validates byte-compilation across all modules. PYTHON= pins the
# interpreter to the 3.9 build even on RHEL 8 where `python3` is 3.6.
make check PYTHON=%{python3_bin} PYSITELIB=%{_python3_sitelib}

%install
rm -rf %{buildroot}
# smartmet-monitor payload (smtop, bstat-family, shared library,
# man pages, README, perf-event-paranoid.md, sysctl drop-in, the
# smartmet_top Python package).
make install \
    DESTDIR=%{buildroot} \
    PREFIX=%{_prefix} \
    PYSITELIB=%{_python3_sitelib}
# smartmet-webmon payload (smwebmon binary, smartmet_webmon Python
# package, browser static assets, systemd unit, sysconfig template,
# clusters.conf, modules-load.d kheaders pre-load, man page).
# The install-webmon target is disjoint from install (different file
# sets); calling both in sequence is the standard subpackage flow.
make install-webmon \
    DESTDIR=%{buildroot} \
    PREFIX=%{_prefix} \
    PYSITELIB=%{_python3_sitelib} \
    UNITDIR=%{_unitdir} \
    SYSCONFDIR=%{_sysconfdir}

%post
# bcc-tools (offcputime-bpfcc, biolat-bpfcc, runqlat-bpfcc) need
# either /lib/modules/$(uname -r)/build to be a valid directory or
# /sys/kernel/kheaders.tar.xz to exist. A noarch RPM cannot Require
# kernel-devel for the running kernel — `Requires: kernel-devel`
# resolves at install time on whatever's newest in the repo, which
# is routinely newer than the running kernel on hosts that haven't
# rebooted since their last update. Surface the mismatch here
# instead of letting the operator discover it later via the panel's
# install hint. -d follows symlinks, so the check catches both
# "missing" and "dangling symlink (wrong version installed)".
# May fire spuriously during a combined install since the
# smartmet-webmon %post (which runs after this one) modprobes
# kheaders; the warning is informational and the operator can verify
# post-transaction with `ls /sys/kernel/kheaders.tar.xz`.
KREL=$(uname -r)
if [ ! -e "/sys/kernel/kheaders.tar.xz" ] && [ ! -d "/lib/modules/$KREL/build/" ]; then
    cat <<EOF

NOTE: smartmet-monitor's bcc-backed flame modes (off-CPU, biolat,
runqlat) will fail until kernel headers are available for the
running kernel ($KREL). Either:

    sudo dnf install kernel-devel-$KREL    (matching headers)
    OR
    sudo modprobe kheaders                  (immediate workaround;
                                             writes /sys/kernel/kheaders.tar.xz
                                             which bcc-tools also accept)

Other smtop features (URLs, log analysis, Proc memory/IO, on-CPU
flame) work without this. See
/usr/share/doc/smartmet-monitor/perf-event-paranoid.md.

EOF
fi

%post -n smartmet-monitor-web
%systemd_post smartmet-webmon.service
# Load kheaders without requiring a reboot. Persistent across reboots
# via /usr/lib/modules-load.d/smartmet-perf.conf. The || : guard
# keeps package install from failing on kernels that don't expose
# the module; the dashboard will surface the resulting per-sampler
# errors via /api/flame/status. The perf paranoid sysctl is shipped
# (commented out) by smartmet-monitor and intentionally not applied
# here — that knob belongs to the host's hardening baseline owner.
modprobe kheaders >/dev/null 2>&1 || :

%preun -n smartmet-monitor-web
%systemd_preun smartmet-webmon.service

%postun -n smartmet-monitor-web
%systemd_postun_with_restart smartmet-webmon.service

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
# Vendor sysctl drop-in. Every line commented out: the file is a
# discoverable cheat-sheet for the Flame-panel paranoid setting,
# never an instrument that lowers host policy on install. A site
# /etc/sysctl.d/99-smartmet-perf.conf overrides this on
# systemd-sysctl reload. %config(noreplace) so an operator who
# uncomments the line directly here (instead of in /etc/) keeps
# their edit across upgrades. smartmet-webmon depends on
# smartmet-monitor at the same version, so the file is guaranteed
# present whether only the CLI or the dashboard is installed.
%config(noreplace) %{_prefix}/lib/sysctl.d/99-smartmet-perf.conf

%files -n smartmet-monitor-web
%{_bindir}/smwebmon
%{_datadir}/smartmet/webmon/
# RIR delegated-stats snapshot for the Countries panel and IP Flow
# rim country labels. Test-phase bundle (~40 MB); long-term to be
# replaced by an explicit refresh mechanism.
%{_datadir}/smartmet/country-db/
%{_unitdir}/smartmet-webmon.service
%config(noreplace) %{_sysconfdir}/sysconfig/smartmet-webmon
%dir %{_sysconfdir}/smartmet-webmon
%config(noreplace) %{_sysconfdir}/smartmet-webmon/clusters.conf
%{_prefix}/lib/modules-load.d/smartmet-perf.conf
%{_python3_sitelib}/smartmet_webmon/
%{_mandir}/man1/smwebmon.1*

%changelog
* Thu Jun 25 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.6.25-1.fmi
- Build arch-specific instead of noarch (dropped `BuildArch: noarch`).
  The payload is arch-independent, but a noarch build lands in the
  single shared `smartmet-open-noarch` repo that every distro reads
  from, so the el8 build — which Requires python39 / python(abi) = 3.9
  — became a candidate on RHEL 10 and failed dependency resolution
  there (no python39 / python3.9 on el10). Building per arch routes
  each build into the existing per-distro x86_64 repos operators
  already consume, so el8 and el10 builds never collide. The
  per-distro `%%if 0%%{?rhel} == 8` python dependency logic is
  unchanged; this just stops the el8 RPM from leaking cross-distro.
- smartmet_top.__version__ / smartmet_webmon.__version__ bumped to
  26.6.25 to track the spec Version (the Makefile names the tarball
  from smartmet_top.__version__).

* Mon Jun 15 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.6.15-1.fmi
- bstat/bchart/burls/bkeys and smtop now ignore the size_t(-1) byte
  count (18446744073709551615 ≈ 2^64) that spine logs for chunked /
  streamed responses with no declared content length. On a busy
  server serving such responses the sentinel swamped every byte sum,
  producing nonsensical avg_KB / MB_out columns (~2^54) and useless
  size / bandwidth bars. Byte values >= 2^53 (or negative) are now
  treated as 0 bytes; request counts, latency and status are
  unaffected. The underlying spine logging issue is separate.

* Wed May 20 2026 Andris Pavēnis <andris.pavenis@fmi.fi> - 26.5.20-1.fmi
- Subpackage renamed: `smartmet-webmon` → `smartmet-monitor-web`.
  The new name follows the `<basepkg>-<feature>` convention used
  elsewhere in the smartmet-* ecosystem and removes the ad-hoc
  "webmon" coinage. `Obsoletes: smartmet-webmon < %%{version}-%%{release}`
  and `Provides: smartmet-webmon = %%{version}-%%{release}` on the
  subpackage so:
    * `dnf upgrade` cleanly replaces an installed `smartmet-webmon`
      (whether the historical standalone RPM or the brief
      26.5.5-x..26.5.19-1.fmi subpackage form) with
      `smartmet-monitor-web` in one transaction.
    * Anything in the wider ecosystem that still says
      `Requires: smartmet-webmon` keeps resolving without a
      coordinated rename.
- Unchanged on purpose (operator muscle memory): the binary name
  `smwebmon`, the systemd unit `smartmet-webmon.service`, the
  sysconfig file `/etc/sysconfig/smartmet-webmon`, the config dir
  `/etc/smartmet-webmon/`, and the Python module `smartmet_webmon/`.
  Only the RPM identifier changes; `systemctl start smartmet-webmon`
  still does what it did before.

* Tue May 19 2026 Andris Pavēnis <andris.pavenis@fmi.fi> - 26.5.19-1.fmi
- smartmet-webmon.spec merged into smartmet-monitor.spec as a
  %%package -n subpackage. One `rpmbuild -bb smartmet-monitor.spec`
  now produces both noarch RPMs (smartmet-monitor and
  smartmet-webmon); the subpackage keeps its
  `Requires: smartmet-monitor = %%{version}-%%{release}` exact-version
  pin, so the two stay in lockstep through upgrades. Verified
  on RockyLinux 9 and 10; RockyLinux 8 build hits a separate
  unrelated RPM conflict that will be investigated separately.
- Makefile: dropped the `webmon-rpm` target since `make rpm` now
  emits both packages from the single spec. `make rpms` is kept
  as an alias for backward compatibility with operator workflows.
- BuildRequires: systemd-rpm-macros added (the smartmet-webmon
  subpackage's %%post / %%preun / %%postun scriptlets use
  %%systemd_post / %%systemd_preun / %%systemd_postun_with_restart).
- smartmet_top.__version__ and smartmet_webmon.__version__ both
  bumped to 26.5.19; the webmon module had been lagging at 26.5.2
  since the cluster-view bumps, and the merge is the natural point
  to re-align them.

* Tue May 05 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.5-2.fmi
- New ``smartmet_top.sources.geo`` module: parses RIR delegated-stats
  files (APNIC / RIPE / ARIN / LACNIC / AFRINIC) into a bisect-backed
  IP→country index. Pure stdlib — no pip dep, no licence tracking,
  no signup. Lookup is ~µs; load of all five RIR files (~325 k
  netblocks) takes under 1 s. Used by the Countries panel and the
  IP Flow panel's hot-IP rim labels in smartmet-webmon.
- New ``smartmet_top.snapshots.countries`` snapshot: aggregates
  ``Store._ipflow_minutes`` by country code on demand. Two readers:
  ``timeline()`` for a multi-line per-country chart, ``table()`` for
  the panel's main view (cc, reqs, bytes, err %%, distinct IPs, top
  IPs from that country).
- smartmet-webmon: New Countries panel — per-country view of
  access-log traffic derived from RIR delegated-stats files.
  Header chart is a multi-line series (one line per top-N country,
  plus "other" for the long tail) over the retained history; the
  table below ranks countries by request count and surfaces the
  busiest IPs from each. Endpoints:
  ``/api/countries/status`` (whether a DB is loaded),
  ``/api/countries/timeline?minutes=N&top_n=K``,
  ``/api/countries?minutes=N&top_n=K``.
- smartmet-webmon: IP Flow rim labels — hot-IP labels now include
  the 2-letter country code when a country DB is loaded
  (``8.8.8.8 US``). Falls back to plain IP when no DB is
  configured.
- smartmet-webmon: New ``--country-db PATH`` CLI flag. Default
  search: ``/var/lib/smartmet-monitor/`` then ``/tmp/smartmet-rir/``.
  The Countries panel renders an empty-state explanation when no
  DB is found; the IP Flow panel keeps working unchanged.
- smartmet-webmon: IP Flow panel — animator rewritten around a
  record-time playhead that walks forward at ``speed × wallclock``
  and spawns each request as a particle when it crosses the
  request's timestamp. Replaces the previous "spawn on poll
  arrival" model, which only produced visible motion when fresh
  log lines were arriving in real time and effectively froze on a
  ``--replay``-fed dev box after the initial burst. Two new
  buttons (Replay 1h / Replay 24h) jump the playhead back and
  play history forward; clicking any point on either timeline
  chart starts scrubbing from that minute. Speed selector (1× /
  10× / 60× / 300× / 1800×) lives in the header.
- smartmet-webmon: Timeline cursor walks rightward at the
  selected speed via a CSS-positioned cursor div on each chart,
  redrawn at RAF cadence without redrawing the chart canvas
  underneath.
- smartmet-webmon: Particle visual lifetime floored at 200 ms
  wallclock so even 100 ms requests stay perceptible at 1800×
  replay speed.
- smartmet-webmon: Cold-IP rim ticks dropped (only the top 16 hot
  IPs get a tick + label); the rim no longer crowds with hundreds
  of unreadable dots on a busy backend. Particles still spawn at
  every IP's correct angle, with or without a static tick.
- smartmet-webmon: Layout selector — ``numeric`` (default —
  ``angle = ip_int * 360 / 2**32``, /24 neighbours cluster) vs
  ``spread`` (rank-based even distribution, better readability
  when a few IPs dominate).
- smartmet-webmon: New encoding legend strip below the topology
  canvas: colour = HTTP status, speed ∝ 1/latency, radius ∝
  log10(bytes), angle by IP layout.
- smartmet-webmon: ``/api/ipflow/window`` ``seconds`` cap raised
  from 300 to 86400 so a Replay-24h fetch can pull the full
  retained history in one shot. The snapshot's max_records=200k
  cap still bounds the response size.
- smartmet-webmon: IP Flow ``spread`` layout now uses a per-IP
  FNV-1a hash instead of rank-based ordering. Rank-based placed
  the busiest IP at 0°, which on a backend with one dominant
  client (e.g. AWS at 80 %% of traffic) made every particle pile
  up on the right side of the canvas. Hash-based gives a
  deterministic, near-uniform angular slot per IP independent of
  count.
- smartmet-webmon: IP Flow rim shows tick marks for **every** IP
  again, with a subtle 1.3 px low-alpha dot, while the labelled
  hot-IP set bumped from 16 to 32. The earlier "no cold ticks"
  change made the long tail invisible; restoring the dots without
  crowding the labels gives both the full distribution and the
  readable busy clients.
- smartmet-webmon: IP Flow timeline cursor no longer hides itself
  when the playhead falls outside the chart's rendered time
  range. It clamps to the chart edge so the vertical line and the
  ``scrub @ HH:MM:SS`` text always agree.
- smartmet-webmon: RIR delegated-stats snapshot bundled in the
  webmon RPM under ``/usr/share/smartmet/country-db/`` so the
  Countries panel and the IP Flow rim country labels work
  out-of-the-box on a fresh install. ``CountryDB`` searches
  ``/var/lib/smartmet-monitor/`` first (operator override), then
  the bundled snapshot, then the dev-box ``/tmp/smartmet-rir/``
  path. Test-phase only — replaced later by an explicit refresh
  mechanism so the RPM stops shipping ~40 MB of dated data.
- smartmet-webmon: IP Flow now has a **service** dropdown in the
  panel header, letting the operator filter the topology +
  timeline charts to one access-log source ("wms", "timeseries",
  "wfs", …) or "all". The dropdown auto-populates from
  ``/api/ipflow/timeline``'s ``sources`` field so newly-spawned
  plugins appear without a panel reload. Backend: per-record
  ``_ipflow_minutes`` tuple gained a 6th field (source_label),
  ``Store.ipflow_window`` and ``Store.ipflow_timeline`` accept a
  ``source=`` filter, and ``Store.ipflow_sources`` enumerates
  labels with retained traffic.
- smartmet-webmon: IP Flow timeline charts switched to
  ``smChart.drawLine`` so they inherit the dashboard-wide hover
  tooltip (vertical guide + value + time at cursor); start /
  midpoint / end time labels still drawn as a small overlay.
- smartmet-webmon: IP Flow timeline charts doubled in height
  (80 → 160 px) for better trend resolution, especially when
  zoomed out to 24 h.
- smartmet-webmon: IP Flow cursor div now shows correctly:
  explicit display:block (the empty string was inheriting the
  stylesheet's display:none) and offsetTop relative to the
  chart-wrap so the line lands over the canvas, not the title row
  above it.
- smartmet-webmon: IP Flow preset buttons (Live / Replay 1h /
  Replay 24h / Pause) show their active state via a coloured
  outline + slight bg tint via a new ``.btn.active`` rule; the
  dispatcher tracks the last user intent so a click on the
  timeline (which also enters scrub mode) doesn't visually
  conflict with the Replay buttons.
- smartmet-webmon: New global "replay in progress" banner: while
  ``runtime.replay_logs`` is bulk-loading the access-log tails at
  startup, the dashboard surfaces a top-of-page strip with
  elapsed seconds + file count instead of leaving the operator
  staring at empty panels. Polls ``/api/health`` (now exposing
  ``replay``) on every refresh tick.
- smartmet-webmon: IP Flow live mode — smooth burstiness. Spine's
  access-log cleaner thread (spine/ContentHandlerMap.cpp:588)
  flushes log lines every 5 s, so records arrive at smwebmon in
  5-second bursts. Live mode now uses the same playhead-driven
  spawn loop as scrub mode, with the playhead held 10 seconds
  behind wallclock; each batch sits in the pending queue and
  drips out smoothly at 1× wallclock as the playhead crosses each
  record's timestamp. Operator sees the panel run ~10 s behind
  real time but with a continuous flow that matches the cadence
  at which the requests originally arrived.
- Bug fix: ``Store.ipflow_sources`` no longer crashes
  ``/api/ipflow/timeline`` with HTTP 500 when ``tail_many`` has
  registered a source label but no requests have parsed yet.
  The previous predicate referenced a non-existent
  ``SourceStats.history`` attribute; the fix uses ``last_seen``,
  which is correctly populated only after the first record.
- smartmet-webmon: Bug fix — IP Flow service dropdown now
  repopulates correctly when the operator navigates away from the
  panel and back. The closure-scoped dedup cache was persisting
  across panel re-init while the ``<select>`` element itself got
  rebuilt fresh each time, leaving the dropdown stuck at "all" on
  revisit.
- smartmet-webmon: Replay banner now shows per-file progress
  (``5 / 22 files``) and the basename of the file currently being
  parsed, so the operator can tell whether a long-running replay
  is making progress or stuck on a single huge file.
  ``bulk_load`` updates ``store.replay_status`` after each file.
- ``bulk_load`` now pre-flights each file with ``os.stat`` and
  skips zero-byte regular files, missing files, and non-regular
  files (FIFOs, sockets, etc.) before handing them to the
  executor. Previously, an exotic disabled-logger setup (e.g.
  ``frontend-access-log`` reduced to zero bytes when the
  frontend daemon's logging was switched off) could leave the
  replay banner stuck at ``N-1 / N files`` indefinitely. The skip
  still increments the per-file counter so the banner reaches
  ``N / N`` and clears.
- ``_bulk_load_one_file`` now stops at the file's size as captured
  at ``open()`` time, instead of letting ``for line in fh`` run
  to natural EOF. On a live access log that smartmetd is actively
  writing to, the previous code could read indefinitely as the
  file kept growing under our cursor, blocking the replay queue
  and (because of disk-bandwidth contention) appearing to
  "freeze" the daemon's own access-log writes. ``.gz`` archives
  are still read to natural EOF since rotated logs aren't growing.
- New ``Store.record_requests_bulk(records, source_label)`` for
  the replay path. Maintains only the aggregates the IP Flow,
  Overview, and Plugins-timeline panels need (``_global_minutes``
  count/bytes/errors, per-source minute_buckets, and the
  per-record ``_ipflow_minutes`` retention) and skips per-URL
  histograms, per-API-key stats, per-source per-second buckets,
  and the raw-line ring. ``bulk_load`` batches records 5000 at a
  time and amortises the store's RLock across the whole batch.
  Replay-mode ingest goes from ~134 k records/s to ~507 k
  records/s on RHEL 8 / Python 3.9 — about 4× faster — and the
  URL / API Keys panels refill from the live tail within seconds
  of replay completing. Combined with the 3× parser speedup,
  end-to-end replay is roughly 8× faster than before.
- ``bulk_load`` calls ``posix_fadvise(SEQUENTIAL | NOREUSE)`` on
  every replay file so the multi-GB read no longer evicts spine's
  hot pages from the page cache. This reduces (but does not
  eliminate) the contention with spine's access-logger cleaner
  thread.
- smartmet-webmon: New ``--replay-live-bytes N`` CLI flag
  (default 0 = skip live files entirely). On spine versions where
  the access-logger cleaner thread holds its WriteLock for the
  duration of every disk flush, our reader and the cleaner
  contend at the filesystem inode-mutex level, stalling
  smartmetd's request handlers for the duration of the live-file
  replay. Skipping the live file at startup sidesteps this; the
  live tail picks up new writes as they arrive (~5 s of empty IP
  Flow timeline, then it populates from incoming traffic).
  Operators on a spine build with the WriteLock-during-IO fix
  (HandlerView three-phase cleanLog) can pass
  ``--replay-live-bytes=-1`` to treat live files like rotated
  ones, or a positive number to read only the trailing N bytes
  for a quick partial replay.
- Access-log parser sped up by ~5× peak / ~3× on a realistic
  burst pattern (1.5 M / 932 k vs 297 k lines/s on RHEL 8 /
  Python 3.9). Four changes:
   * The 10-line regex was replaced with ``str.split()`` over the
     13 fixed space-separated tokens — safe because URLs are
     URL-encoded and never contain literal spaces. Validation is
     just the field count plus the three int conversions on
     STATUS / DUR_MS / BYTES.
   * Timestamp parser hand-rolled with a per-date midnight-epoch
     cache so ``time.mktime`` (consults local tz files) runs once
     per unique date in the replay set, not per line.
   * 1-deep last-seen cache on ``parse_iso`` keyed by
     ``YYYY-MM-DDTHH:MM:SS``. The access-log cleaner flushes in
     5-second bursts so consecutive records usually share the
     same wall-clock second; on a cache hit the parse collapses
     to a string equality test (~65 ns).
   * Same-minute fast path. When the cache key differs only in
     the seconds digits, recompute by ASCII-arithmetic delta on
     the two digit pairs and add to the cached epoch. Catches
     every record inside a single ``MM`` boundary regardless of
     how many distinct seconds occur in it. ~140 ns/call.
  Fractional seconds are dropped — no consumer uses sub-second
  precision. End-to-end: a multi-GB replay's parse slice shrinks
  to roughly a third of the regex-era cost.

* Tue May 05 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.5-1.fmi
- New IPFlow data path: ``Store.record_request`` accepts an ``ip``
  argument (default empty), and a per-minute ``_ipflow_minutes``
  ring retains raw ``(ts, ip, dur_ms, bytes, status)`` tuples
  pruned by the same HISTORY_MINUTES window everything else uses.
  ``smartmet_top.snapshots.ipflow`` exposes ``timeline()`` and
  ``window()`` readers plus a stable ``angle_for_ip(ip)`` mapping
  ``ip_int * 360 / 2**32`` so /24 neighbours sit at adjacent
  angles. Used by the smartmet-webmon IP Flow panel.
- smartmet-webmon: New IP Flow panel — animated topological view
  of access-log traffic. Two stacked timeline charts at the top
  (req/min, bytes/min) span the retained history and act as the
  scrubber — click anywhere to pin the topology view to that
  minute. The topology canvas underneath places client IPs at
  fixed angles around the rim (``angle = ip_int * 360 / 2**32``,
  so /24 neighbours sit at adjacent angles), and each request
  becomes a particle that flies from its IP's slot to the centre
  over its ``dur_ms``. Speed encodes latency, colour encodes
  status (green 2xx / blue 3xx / amber 4xx / red 5xx), radius
  encodes ``log10(bytes)``. Header controls: history depth,
  window length, top-N filter (10/25/50/100/all), Live, Pause.
  Pause freezes both polling and the RAF loop so the operator can
  study a moment without it being overwritten by the next poll.
  Endpoints: ``/api/ipflow/timeline?minutes=N`` and
  ``/api/ipflow/window?start=T&seconds=N&top_n=K``.
- smartmet-webmon: Bumped ``smartmet_webmon.__version__`` from
  the lagging 26.5.2 to 26.5.5 so dashboard runtime metadata
  matches the package version.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-11.fmi
- CachesSnapshot.trends() and ServicesSnapshot.trends() now emit
  step_seconds + last_ts at the response top level. The 26.5.4-10
  fix added the JS-side plumbing for these but missed that the
  trends endpoints (distinct from the cluster_chart endpoints,
  which already had them) didn't actually return the fields —
  result was the per-row sparkline tooltip kept rendering value-
  only with no time. Same plumbing as the Plugins fix in 26.5.4-10.
- NetworkSnapshot.detail() emits step_seconds + last_ts (extracted
  from the netstats sample deque). Without it the Network panel's
  TCP / state / per-NIC charts had value-only tooltips.
- smartmet-webmon: Network panel charts (TCP retransmit, per-state
  trend, per-NIC rx/tx) now show time-at-cursor in the tooltip.
  The four drawLine / drawSparkline call sites needed last_ts +
  step_seconds threaded through; the snapshot now provides them.
- smartmet-webmon: Caches and Services per-row sparklines pick up
  the new step_seconds / last_ts fields the snapshots now return;
  the JS side from 26.5.4-10 was already passing them whenever the
  response contained them, so this part is server-side-only.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-10.fmi
- Plugins panel: drop the "1m" entry from the window selector. It
  shared the visual meaning of the existing "60s" entry but mapped
  to minute_window(1), which returns only the *current* incomplete
  minute bucket — empty 0-60 s after a minute boundary regardless of
  traffic, indistinguishable from "60s" by label, and confusing in
  every way. Operators wanting a 1-minute view continue to use "60s",
  which merges 60 finalised per-second buckets and is the right
  implementation for that visual meaning.
- PluginsSnapshot.trends() now emits step_seconds + last_ts alongside
  the per-source rows. Mirrors what CachesSnapshot.trends() and
  ServicesSnapshot.trends() already returned. The browser threads
  these through to drawSparkline so the per-row hover tooltip shows
  the time at the cursor — previously the Plugins / Caches / Services
  per-row sparks rendered value-only on hover, with no time, which
  was disorienting whenever the time axis was coarse.
- smartmet-webmon: Per-row sparklines in the Plugins, Caches, and
  Services tables now show the time-at-cursor in their hover
  tooltip, threaded via the existing last_ts / step_seconds plumbing
  in chart.js. Previously they rendered value-only, which was
  disorienting with coarse time axes. The cluster-mode multi-line
  charts on top of those panels already had time-on-hover; this
  brings the per-row sparks to parity.
- smartmet-webmon: Plugins panel window selector loses the "1m"
  entry (see smartmet-monitor 26.5.4-10 above for the why).

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-9.fmi
- New Heap section in the Proc panel surfaces the SmartMet
  process's allocator stats (jemalloc only — mimalloc text is
  ignored for v1). Polls spine's ?what=mallocstats endpoint at
  30 s cadence (the JSON dump is large and the numbers don't
  change at sub-second rates). Per host: allocated / active /
  resident / mapped / retained bytes, arena count, jemalloc
  version, plus a fragmentation% colour-coded by severity
  (green <15%%, amber 15-30%%, red >30%%) and a sparkline of
  allocated bytes over the retained 15-minute history.
- New `h` hotkey in the Proc panel toggles the Heap section
  visibility, parallel to `m` / `i` / `g`.
- Recommends smartmet-library-spine >= 26.4.27 (the version
  that added the ?what=mallocstats handler — older builds
  return endpoint-not-found and the section renders empty).
- smartmet-webmon: new Heap tab in the dashboard surfaces the
  same allocator stats. Per host: allocated / active / resident /
  mapped / retained / metadata bytes, arena count, jemalloc
  version, fragmentation% (colour-coded), plus a multi-line chart
  of allocated/active/resident over the retained history. Backed
  by /api/heap/detail (JSON envelope with the bounded per-host
  time series) and /api/heap (CSV-style tabular export).

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-8.fmi
- smartmet_filter.is_smartmet_frame() now recognises every namespace
  used inside smartmetd, not just SmartMet::. The earlier matcher
  only caught the SmartMet:: namespace, which silently dropped any
  flame stack rooted in Fmi:: (macgyver/spine/gis), NFmiArea / NFmiPoint
  (newbase legacy class prefix), Giza:: (SVG), Imagine::, Locus::,
  Trax::, Osm::, the grid-files namespaces (GRIB1, GRIB2, NetCDF,
  QueryData, GeoTiff, Map, GRID, Identification), the grid-content
  namespaces (ContentServer, DataServer, QueryServer, Functions, Lua,
  HTTP, Corba, SessionManagement, UserManagement), TextGen / BrainStorm
  / Aggregator / OptionParsers / SpecialParameter / Stat / TimeSeries,
  Delfoi / FlashQuery / OracleUtils / Observation, and Dynlib —
  all of which appear regularly in production smartmetd flames. The
  smartmet-only mode was treating all of them as syscall noise and
  dropping the stack. Surveyed across ~/hub on 2026-05-04 to enumerate
  the exact set.
- NFmi[A-Z] regex catches the legacy global-class convention
  (NFmiArea, NFmiPoint, NFmiQueryData, ...) without false-matching
  anything that just happens to start with those four characters.
- Deliberately excluded: FMI:: (uppercase), DataTransform::,
  RadContour::, TimeTools::, WRFData::, HDF5:: — these only appear
  in fmitools / qdtools, which are CLI binaries that don't run
  inside smartmetd, so including them is at-best ineffective and
  at-worst confusing if a stray test process hits the dashboard.
- make check exercises the new matcher against representative
  symbols including the explicit fmitools/qdtools-exclusion guard
  and the NFmi[A-Z] boundary cases.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-7.fmi
- perftop.py: lower DEFAULT_INTERVAL 10s -> 6s (50%% duty cycle, the
  practical floor for "live-feeling updates that don't visibly hurt
  the host"). The 10 s default was conservative when the recorder
  was new; with `CPUQuota=200%%` already bounding the unit, the
  shorter cycle makes the on-CPU flame feel responsive without any
  realistic risk to the target. --perf-interval / --perf-record-seconds
  defaults updated in smtop and smwebmon argparse to match.
- perftop.py: pass --mmap-pages 256 to perf record. Default 8 pages
  (32 KB) per CPU was overflowing within milliseconds when DWARF
  stack-dumps fly in from a 326-thread process; perf would
  silently downscale sample density. 256 pages (1 MB per CPU)
  gives the recorder room to keep up. Only safe because the
  smwebmon unit was granted CAP_IPC_LOCK in 26.5.4-6 — without
  that, mmap'ing >516 KB hits perf_event_mlock_kb's default and
  fails with "mmap: Operation not permitted".
- proc.py: detect_role now parses --port=8080/8081 from cmdline
  before falling back to the existing string-match on
  frontend/backend keywords. Port is the more reliable signal
  (operator-edited via the systemd drop-in / sysconfig env file)
  versus the cmdline path which can lag the deployment.
  make check covers the user's actual cmdline strings.
- smartmet-webmon: bump cgroup MemoryMax from 512M to 2G. The
  earlier 512M cap was set when the unit only ran perf record;
  once the full bcc-tools kit landed (off-CPU + page-fault +
  wakeup + blockflame + biolat + runqlat) plus DWARF unwinding
  mmap pages plus journalctl-tail, the cgroup peaks at ~860 MB —
  the kernel was OOM-killing smwebmon every few minutes, which
  manifested as "the on-CPU graph keeps clearing for no reason"
  (the bounded perfdata ring lives in process memory, so each
  kill resets it). 2 GB gives headroom for the realistic peak
  without being absurd; CPUQuota=200%% still bounds runaways.
  Diagnosed by reading dmesg's oom-killer output on a live
  backend.
- smartmet-webmon: at startup, check journalctl for an OOM kill of
  the unit in the last 10 minutes. If found, log a clear breadcrumb
  to stderr explaining the cap and how to raise it via
  `systemctl edit`. So the next operator who hits the same
  scenario sees "previous instance was OOM-killed" instead of
  having to chase the empty graph through dmesg.
- Apply the new unit with: sudo systemctl daemon-reload &&
  sudo systemctl restart smartmet-webmon.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-6.fmi
- /usr/lib/sysctl.d/99-smartmet-perf.conf gains a (commented out)
  kernel.kptr_restrict = 0 line alongside the existing paranoid
  line. Surfaced because perf record fails with exit 255 on hosts
  running kptr_restrict=2 — CAP_SYSLOG (which the smwebmon unit
  now grants) bypasses level 1 but not 2, and operators on
  hardened hosts need the sysctl path. File stays fully commented;
  documents the recommended setting alongside the trade-off.
- smartmet-webmon: Cache-Control: no-store on the static asset
  responses (server.py). The handler previously sent no Cache-Control
  on HTML/JS/CSS, so browsers used heuristic caching and kept
  serving the previous flame.js / app.js / style.css after an
  RPM upgrade — turning every "bug fix shipped" into "but the
  user thinks it didn't". no-store on small files served over
  loopback has negligible cost; closes a class of stale-asset
  confusion permanently.
- smartmet-webmon: unit gains CAP_IPC_LOCK and CAP_SYSLOG.
  CAP_IPC_LOCK fixes off-CPU's "mmap: Operation not permitted" —
  perf_event_open's mmap path mlocks the per-CPU ring buffer
  beyond perf_event_mlock_kb's default 516 KB, which an
  unprivileged daemon can't do without it. CAP_SYSLOG bypasses
  kernel.kptr_restrict=1 (the RHEL default) so /proc/kallsyms
  resolves and perf doesn't bail at exit 255. Both are strictly
  narrower than the CAP_SYS_ADMIN already in the unit; same
  justification chain as CAP_DAC_READ_SEARCH from 26.5.4-5.
- smartmet-webmon: Flame panel diagnostics block now scoped to the
  currently-selected mode. Previously every sampler's failure
  surfaced in every flame mode's diagnostics — runqlat / biolat /
  perfstat (which aren't even flame modes) appeared under on-CPU,
  which made no sense in that context. Each mode now shows only
  its own sampler; biolat / runqlat / perfstat failures continue
  to surface in the Proc panel where they belong.
- smartmet-webmon: Flame zoom-out is now discoverable. Three new
  affordances: right-click anywhere on the canvas pops one level
  up (matches flamegraph.com / Speedscope convention); a visible
  "← Zoom out" button next to the breadcrumb does the same; the
  breadcrumb itself got bolder styling — slightly larger,
  full-color text instead of muted, and a subtle background
  strip — so operators stop missing it. The breadcrumb's root
  link still does "all the way out".
- Existing webmon installs picking this up need a
  `systemctl daemon-reload && systemctl restart smartmet-webmon`
  for the new caps to apply, and a force-reload of the
  dashboard (Ctrl+Shift+R) once for the browser to drop its
  pre-no-store cache of flame.js / app.js / style.css.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-5.fmi
- smartmet-webmon: Add CAP_DAC_READ_SEARCH to the smwebmon unit's
  AmbientCapabilities / CapabilityBoundingSet. /sys/kernel/debug
  is mode 700 root:root on RHEL, and CAP_SYS_ADMIN (already
  granted) does not bypass DAC checks — so the bcc-tools that
  create kprobes (offcputime-bpfcc, biolat-bpfcc, runqlat-bpfcc)
  failed with `open(...kprobe_events): Permission denied` even
  after paranoid was lowered. CAP_DAC_READ_SEARCH is the narrow
  fit (read + directory traverse only, not write) and is strictly
  narrower than the CAP_SYS_ADMIN already in the unit, so it does
  not expand the worst-case attack surface.
- Existing webmon installs running this version will pick up the
  new cap on the next `systemctl daemon-reload && systemctl
  restart smartmet-webmon` (RPM upgrade does not auto-restart
  the unit).

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-4.fmi
- smartmet-webmon: Browser-flame zoom is now stable across the
  periodic refresh. flame.js's setData() used to unconditionally
  reset this.zoomPath to [] every cycle, popping the operator out
  of any zoom they had clicked into within seconds. Same conceptual
  bug the smtop TUI fix in 26.5.4-1 addressed Python-side; the
  browser path was missed. Now setData preserves zoomPath as user
  intent and draw() walks a *local* render path up just far enough
  to find a non-empty subtree when a deep leaf is missing from the
  latest refresh — the view springs back to the operator's zoom as
  soon as that leaf reappears.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-3.fmi
- Drop the `Requires: kernel-devel` introduced in 26.5.4-2; restore
  `Recommends: kernel-devel-uname-r`. A noarch package cannot pin
  Requires to the running kernel, so dnf installed whatever
  kernel-devel was newest in the repo (typically newer than the
  running kernel on hosts that haven't rebooted post-update). The
  Requires looked like a guarantee that wasn't there, and operators
  ended up seeing "kernel-devel installed" yet bcc-tools still
  failing with `chdir(.../build): No such file or directory`.
- Flame panel's off-CPU mode now detects the actual condition:
  /lib/modules/$(uname -r)/build missing, dangling (wrong version
  installed), or valid (incl. /sys/kernel/kheaders.tar.xz fallback).
  Surfaces a clean warning naming the running kernel and the exact
  `sudo dnf install kernel-devel-$(uname -r)` command, plus the
  `sudo modprobe kheaders` fallback for operators who can't install
  or reboot right now. Replaces bcc's cryptic chdir error with
  something actionable.
- New %post scriptlet runs the same check at install/upgrade time
  and prints the same install hint as the panel — so an operator
  who hits the kernel-devel mismatch sees it during `dnf install`,
  not later when they open the Flame panel for the first time and
  wonder why nothing renders.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-2.fmi
- Require kernel-devel so bcc-tools (offcputime-bpfcc, biolat-bpfcc,
  runqlat-bpfcc) find the in-tree kernel headers at runtime instead
  of failing with `chdir(/lib/modules/$(uname -r)/build): No such
  file or directory`. The matching kernel-devel for the running
  kernel is the operator's responsibility after kernel updates —
  a noarch package cannot pin to the running kernel version.
- Take ownership of /usr/lib/sysctl.d/99-smartmet-perf.conf from
  smartmet-webmon, ship it with kernel.perf_event_paranoid commented
  out, %config(noreplace) so operator edits survive upgrades.
  Installing smartmet-monitor no longer modifies host security
  policy — the change belongs to the host's hardening baseline owner,
  who uncomments the line (or sets it in /etc/sysctl.d/) once the
  decision is made. smartmet-webmon's %post correspondingly stops
  running `sysctl --system`; it still loads the kheaders module.
- Flame panel modes that need a lower paranoid (off-CPU at >1,
  wakeup / blockflame / pagefault at >0) now display a clean
  "kernel.perf_event_paranoid=N, this panel needs <=M" warning with
  the file path and `sudo sysctl --system` command, replacing perf's
  misleading native "event syntax error" relay.

* Mon May 04 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.4-1.fmi
- Flame zoom is now stable across recorder cycles. The zoom path
  used to be eroded each time the ring rebuilt (~3 s) when the
  exact deep leaf the operator zoomed into was not sampled in
  the latest cycle, walking the view back to root within seconds.
  The walk-back is now render-only — self.zoom_path is preserved
  as user intent, and the view springs back to the operator's
  zoom as soon as the leaf reappears in a later ring.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-14.fmi
- Flame panel: press `a` to freeze every recorder ring and run six
  anti-pattern detectors against the frozen stacks (locale-lock on
  stream construction, per-request regex compile, per-request DNS,
  per-request GDAL/PROJ init, lock-holder/waiter pair, major-fault
  working-set pressure). Findings list with severity / share% /
  hint; Enter on a finding zooms the flame to the evidence stack.
  Press `a` again to resume recording.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-13.fmi
- smartmet-webmon: New cluster Proc panel — per-backend memory
  (RSS), IO read / write rates, thread counts, and major page-fault
  rates as multi-line overlays — one line per backend, color-hashed
  the same way as every other cluster panel. The admin plugin does
  not serve /proc data, so the architecture is to fan out across
  each backend's *own* smwebmon: when a cluster's clusters.conf
  has a ``webmon-url-pattern`` set, the cluster discovery loop
  probes each backend's smwebmon at /api/health on every cycle,
  and the Proc panel calls the Proc-capable backends'
  /api/proc/detail in parallel at refresh time.
- smartmet-webmon: new ``BackendInfo.webmon_ok`` flag tracked per
  backend; the cluster discovery_status string now reports e.g.
  ``ok (5/6 alive, 4 with smwebmon)`` so the operator can see at
  a glance how many backends are wired up for the cluster Proc
  panel.
- smartmet-webmon: New cluster-scope endpoint
  /api/cluster/proc/detail returns ``{configured, backends:
  {prefix: {latest, series}}, errors}``. Single-host Proc panel
  unchanged: the panel branches on the active-cluster state, and
  renders the existing per-PID detail view when no cluster is
  selected.
- smartmet-webmon: README "Cluster Proc panel" subsection
  documents the three-step setup (install smwebmon on each
  backend, bind to routable address, add webmon-url-pattern),
  with explicit security note about smwebmon being unauthenticated
  and the expectation of firewall-level restriction. clusters.conf
  template gets the optional key alongside admin-url-pattern.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-12.fmi
- smartmet-webmon: Collapsed cards no longer leave dead space in
  the grid. The click-to-collapse from -10 hid only the card body;
  the card itself kept its grid cell, so adjacent cards could not
  reflow into the freed space. Now a collapsed card is fully
  removed from layout (``display: none`` on the whole card), and
  CSS Grid's ``auto-fit, minmax(420px, 1fr)`` automatically widens
  the remaining cards to fill the freed columns.
- smartmet-webmon: A "hidden:" chip strip appears at the top of any
  panel with collapsed cards, listing each one as a clickable pill
  (e.g. ``hidden: [Memory ▸] [Page-faults ▸]``). Clicking a chip
  restores that card to the grid and removes the chip. The strip
  is empty when nothing is hidden, in which case CSS ``:empty``
  collapses it so the panel keeps its full chrome.
- smartmet-webmon: Each hidden card's title is persisted in
  localStorage alongside the ``collapsed: true`` flag, so the chip
  strip re-renders correctly across page reloads even before the
  panel's first refresh has built the cards.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-11.fmi
- Smtop section-header convention: every multi-section curses panel
  (Network and Proc today) renders its section dividers as ``▾ [k]
  Section Title ──────`` with the letter in red bold inside square
  brackets and a ``▾`` chevron that rotates to ``▸`` when the
  section is hidden. The bracket-prefix style coexists with section
  titles that are inherently all-caps (TCP, I/O) — the chip is a
  separate visual token, not a letter embedded in the title.
- Section toggle keys on the Network panel: ``t`` TCP host-wide,
  ``c`` Connection states, ``l`` Listen sockets, ``b`` Per-NIC
  bandwidth. Pressing the lowercase letter hides or shows that
  section; the freed vertical space flows to the remaining
  sections automatically (each section's body is gated behind the
  visibility set, the headers always render so the operator can
  see what's available even when collapsed). Mirror toggles on
  Proc: ``m`` Memory, ``i`` I/O, ``g`` paGe-fault rate. The
  optional sampler-gated sections (vmstats, biolat, runqlat,
  perfstat, netstats compact summary, perf, smaps rollup) keep
  their pre-existing "show only when data exists" gating since a
  letter on top would be redundant — they hide naturally.
- Proc panel paired-cycle widget: bottom-of-panel legend renders
  ``< b PID n >`` with ``b`` and ``n`` in red, where ``b`` cycles
  the focused smartmetd PID backward and ``n`` forward. The angle
  brackets are visual arrow cues; the word ``PID`` between them
  is the noun being navigated. ``b`` is new (was previously only
  ``n`` for forward); 1-9 still jump to a PID by its red
  ``[N]``-mnemonic row.
- Footer chips on both panels also show toggle state — a section's
  ``[t]`` chip is red+bold when visible, dim grey when hidden, so
  the operator gets a glance-state without scanning section
  headers.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-10.fmi
- smartmet-webmon: Click any card heading to collapse / expand it.
  Every ``.section-card`` in every panel becomes collapsible — the
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
- smartmet-webmon: Per-card vertical resize was deliberately
  deferred. Native ``resize: vertical`` works in the browser but
  it fights with the canvas-redraw cycle in panels that rebuild
  HTML on each refresh, and operators have not asked for height
  control beyond the existing per-canvas defaults. If they do, a
  ResizeObserver + targeted-canvas-redraw approach is the natural
  extension; the localStorage state shape already reserves a
  per-card ``height`` field so a future commit can add it without
  a schema bump.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-9.fmi
- smartmet-webmon: Hover tooltip now works on every chart in the
  dashboard, not only the chrome-equipped drawLine/drawLineMulti
  charts. ``drawSparkline`` was previously chromeless on purpose
  (no axis ticks, no padding — it lives in tight per-row table
  cells), and in service of that purity it had no hover handler.
  Operators reported tooltips missing on Plugins / Services /
  Caches per-row trend sparklines and on the Network
  Connection-states per-state mini-charts. Now those sparklines
  wire the same pinned-Y tooltip the line charts use, with no
  chart redraw and no vertical guide (the cell is too small for
  that chrome) — just a value-and-time readout on hover. The
  tooltip's Y stays anchored to the canvas top edge, same
  anti-bounce rule as before.
- smartmet-webmon: Per-row call sites pass the right ``fmtY`` so
  the tooltip renders values in their natural unit (latency in ms
  via formatMs, bytes via formatBytes, request rates as integers,
  hits/min with one decimal, connection counts as integers).

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
- smartmet-webmon: same default change applied to the CLI; pass
  an empty string to disable; pass any single unit name to opt
  back into the old single-unit behavior.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-5.fmi
- Bump smartmet_top.__version__ and smartmet_webmon.__version__ to
  26.5.2 (the spec versions had been bumped over the cluster-view
  Phase 2/3 work, but the Python package metadata still reported
  26.4.30 at runtime). The Makefile's source-tarball name is
  derived from __version__, so this also fixes the make-rpms
  failure where rpmbuild looked for smartmet-monitor-26.5.2.tar.gz
  while git archive was producing smartmet-monitor-26.4.30.tar.gz.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-4.fmi
- smartmet-webmon: Cluster on-demand lastrequests fetches now share
  a 10 s server-side cache keyed on (cluster, minutes). Without
  it, the URL drill-down modal's 2 s panel refresh fired N parallel
  admin-plugin fetches at minutes=60 every refresh — for a 6-backend
  cluster that was 180 large lastrequests calls per minute per
  cluster. With the cache, the first chart refresh after a TTL
  window does the parallel fetch and everything within the window
  (the modal's 2 s tick, multiple chart endpoints serving the same
  panel — URLs / Plugins / Keys / Overview all default to minutes=60
  so they share one fetch) reuses the result. Backend admin-plugin
  load drops by ~5×; the chart still feels live with ~10 s update
  granularity. The TTL is short enough that a backend coming back
  online appears in the chart within one cycle without operator
  intervention.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-3.fmi
- _build_cluster_chart now omits errored backends from the chart
  series (the legend still surfaces them with a warning marker).
  Backing the smartmet-webmon cluster multi-line chart UX fix.
- smartmet-webmon: Cluster multi-line charts no longer draw a
  misleading flat-zero line for backends whose lastrequests fetch
  failed. The errored backends are now omitted from the chart
  series; they appear in the legend with a ⚠ marker, color-hashed
  to their normal color but drawn at 40 %% opacity, with the
  failure reason as a tooltip. Previously a "fetch failed" backend
  was indistinguishable from a "no traffic for this URL/plugin/key"
  backend on the chart — both rendered as a flat line at zero. Now
  the legend tells the operator the truth.
- smartmet-webmon: Refactored the four panel legend builders (URLs /
  Plugins / Keys / Active) to share a single _buildClusterLegend
  helper. Cuts ~80 lines of duplicated DOM-building. The
  errored-prefix pass at the end ensures the legend lists every
  prefix the cluster polled, not only the ones the chart shows.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-2.fmi
- smartmet-webmon: Cluster-view Phase 3: backend-pill topology strip
  below the top bar (cluster mode only). One pill per backend prefix;
  the dot inside each pill is the same color the chart legends use,
  so identifying which backend a line belongs to is a single glance.
  Hover surfaces the backend's handler list (truncated at 40
  entries with a "…and N more" marker for grid-content-heavy
  prefixes like q3 satellites). A backend that is registered
  but has no handlers in clusterinfo (offline / draining /
  paused) renders muted with a strikethrough.
- smartmet-webmon: Topology refreshes every 30 s (same cadence as
  the cluster selector dropdown) but is debounced on a content
  hash, so the operator's mid-hover position is not lost on idle
  refreshes.
- smartmet-webmon: README documents cluster mode end-to-end:
  topology strip reading guide (healthy shape / trouble pattern /
  typical root cause / where to look next), per-panel data-path
  table (which panels reuse the 2 s polling vs which fire
  on-demand parallel lastrequests fetches at chart-refresh time),
  and the multi-line chart reading guide.

* Sat May 02 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.5.2-1.fmi
- smartmet-webmon: Cluster-view Phase 2c (Plugins / Keys) and Phase
  2d (Overview): the remaining cluster-mode multi-line trend charts.
  With this commit every panel that produces a time-series chart in
  single-host mode now has a per-backend overlay equivalent in
  cluster mode.
- smartmet-webmon: Plugins panel: a "per-backend plugin trend" card
  on top of the table. Pick a plugin (the leading URL path segment,
  derived from the most recent admin lastrequests fetch) and metric,
  see one line per backend. Same color hashing and clickable-legend
  pattern as the URLs/Active/Caches/Services panels.
- smartmet-webmon: Keys panel: same shape, picker is the apikey
  (excluding the dash placeholder). Hover tooltip lists every
  backend's value at the cursor, sorted descending, just like the
  other multi-line charts.
- smartmet-webmon: Overview panel: each of the five mini-charts
  (req/min, mean ms, p95 ms, bytes/min, err %%) becomes a multi-line
  per-backend overlay in cluster mode, with one parallel HTTP fetch
  producing all five metrics — N backend admin calls per panel
  refresh, not 5N. The ``metrics=`` query of
  /api/cluster/overview/chart accepts a comma-separated list and
  returns ``charts: {metric: ...}``. bytes/err_pct fall back to
  the existing single-line endpoint because lastrequests rows do
  not retain bytes/status — a future refactor of _aggregate_minute
  could lift this if operators ask for per-backend bytes
  specifically.
- smartmet-webmon: handlers.py refactor: extracted _resolve_cluster,
  _fetch_cluster_lastreqs and _build_cluster_chart so the four
  on-demand cluster chart endpoints (URLs, Plugins, Keys, Overview)
  share one parallel-fetch + bucket-and-aggregate pipeline. Each
  endpoint is now a thin wrapper specifying its own row_matches
  filter.
- Day rolled past midnight → Version bump from 26.4.30-N to
  26.5.2-1 per the YY.M.D scheme.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-17.fmi
- New CachesSnapshot.cluster_chart_per_host /
  ServicesSnapshot.cluster_chart_per_host snapshot methods backing
  the cluster-mode multi-line trend charts in smwebmon (Phase 2b).
- smartmet-webmon: Cluster-view Phase 2b — Caches and Services
  panels add a per-backend trend chart in cluster mode. Pick a
  cache (or service handler) from the dropdown and the multi-line
  chart shows that entity's metric over time, one line per backend,
  same color hashing as the URLs and Active panels. Metric pickers
  cover hits/min, inserts/min, hit %%, size for caches; req/min,
  req/hour, req/day, avg ms, avg cpu ms for services. Clickable
  legend toggles per-backend visibility.
- smartmet-webmon: Data path is zero extra HTTP: cachestats and
  servicestats are already polled per-host on the 2 s admin
  cadence (one task per backend, asyncio.gather in adminapi.poll_all),
  and the per-host results land in store.cache_history and
  store.service_history. The new cluster_chart_per_host snapshot
  methods just rearrange the existing per-host series into the
  {label, values} shape that drawLineMulti consumes. Cluster size
  scales linearly in storage cost only (no extra requests per
  panel refresh).
- smartmet-webmon: New endpoints /api/caches/cluster_chart and
  /api/services/cluster_chart. Both return the per-host series
  plus the union of available entity names so the UI's dropdown
  stays current as new backends come online (the cluster's
  discovery loop catches added prefixes within ~60 s).
- smartmet-webmon: Single-host mode unchanged: the trend card
  stays hidden and the per-row sparkline-trend column continues
  to be the operator's view. Cluster mode shows the chart card
  above the table; the per-row trends remain so the table is
  still useful for cross-backend at-a-glance scanning.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-16.fmi
- smartmet-webmon: Chart hover tooltip no longer bounces vertically.
  The tooltip box is now pinned to the canvas's top edge in
  viewport coordinates and only its X tracks the cursor (with
  edge-flip when there is no room on the right). Earlier the box
  followed e.clientY, so as the operator's cursor naturally
  tracked peaks and valleys in a busy latency chart the tooltip
  jittered up and down — distracting and hard to read. The
  vertical guide line and per-series dots still appear AT the
  cursor's data points; only the value-readout box is anchored.
- smartmet-webmon: The tooltip is now multi-row for drawLineMulti
  charts: one row per backend with a color swatch, label, and
  value, sorted descending so the busiest backend is at the top.
  (Previously even cluster-mode charts showed only a single
  value.)

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-15.fmi
- smartmet-webmon: Cluster-view Phase 2c — URLs panel drill-down
  chart shows one line per backend in cluster mode. Click a row
  in the URLs table; the modal's "Per-backend latency, last 60 min"
  chart renders an overlay with one line per alive backend (same
  color hashing as the Active panel, so c2 matches across panels).
  A metric picker in the chart header switches between p95 / p50 /
  mean / max / count. Clicking a legend entry hides that backend's
  line.
- smartmet-webmon: Data path is on-demand parallel: when the chart
  refreshes, the cluster-scope handler fires one HTTP request per
  backend (a cluster has ≤10 backends in practice) to
  /admin?what=lastrequests&minutes=60. ThreadPoolExecutor with one
  worker per backend means wall time ≈ slowest backend, not sum.
  The rows are bucketed by minute on the operator-clicked URL and
  the chosen metric is computed per minute. No changes to the
  existing 2 s admin polling — the per-cluster store still gets
  fed by it for the URL table; the chart just reaches around the
  store to get per-host attribution that the store does not
  retain.
- smartmet-webmon: New endpoint /api/cluster/urls/chart
  (cluster-scope) that returns {series: [{label: prefix, values:
  [...]}], errors: {prefix: msg}}. Errors per backend are surfaced
  inline in the legend (a ⚠ next to the backend name with the
  error reason as a tooltip) so a single misbehaving backend
  doesn't fail the whole chart. Single-host mode keeps using
  /api/urls/chart as before; the modal picks endpoint based on
  cluster mode.

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-14.fmi
- smartmet-webmon: Cluster-view Phase 2a — Active panel reshaped
  for cluster mode. When a cluster is selected, the in-flight count
  chart shows one line per backend instead of the aggregated
  cluster-total. Each line gets a stable color (Tableau-categorical
  10-slot palette hashed by backend prefix, so c2 is the same color
  across every panel and every refresh). Clickable legend below
  the chart toggles per-backend visibility. Single-host mode (no
  cluster selected) keeps the existing aggregated single-line shape
  unchanged.
- smartmet-webmon: New chart helper drawLineMulti in chart.js —
  accepts [{label, color, values}, ...] and renders all overlaid
  with shared Y-axis nice-ticks (Heckbert), shared X-axis time
  labels, and a hover crosshair that draws a dot per series at
  the cursor's index plus a vertical guide line. Existing drawLine
  unchanged; the multi variant is a peer.
- smartmet-webmon: New ActiveSnapshot.chart_per_host returning
  per-backend in-flight count series. Existing chart() (aggregated
  total) unchanged; the per-host variant is what the cluster-mode
  UI fetches via ?multi=1 on the existing /api/active/chart
  endpoint. Backwards-compatible: old clients without ?multi=1
  still get the aggregated form.
- smartmet-webmon: Color assignment is deterministic (hash of label
  → palette slot) so operators learn one mapping cluster-wide
  instead of having to re-orient on every panel.

  Phase 2 still has: URLs / Plugins / Caches / Services / Keys
  to reshape (separate commits — Caches and Services already have
  per-host data in Store and are next; URLs / Plugins / Keys need
  per-host accumulation in Store first because their stats are
  currently global tail-derived).

* Fri May 01 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-13.fmi
- smartmet-webmon: Auto-detect cluster naming changed from FQDN-based
  to prefix-family-based. The FQDN approach (split
  <prefix>.<cluster>.<rest> and use <cluster> as the name)
  misidentified two of the three FMI clusters:
  `in1.back.smartmet.fmi.fi` and `c3.back.smartmet.fmi.fi` share the
  `back.smartmet.fmi.fi` domain, so the FQDN-derived name collided.
  `open1.smartmet.fmi.fi` had no `back` segment at all, so the
  FQDN-derived name landed on `smartmet`. The cluster identity is
  actually in the *prefix family* the local frontend routes to:
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
- smartmet-webmon: Operators who want friendlier names (`back`
  instead of `c`, `internal` instead of `in`, `opendata` instead of
  `open`) override via `/etc/smartmet-webmon/clusters.conf`. The
  auto-detect output is "honest about what's observable" rather
  than embedding FMI-specific naming knowledge in the package.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-12.fmi
- smartmet-webmon: Cluster-view groundwork (Phase 1 of 3). The
  dashboard can now monitor multiple SmartMet clusters from a
  single smwebmon instance: cluster discovery via the frontend's
  clusterinfo HTML, per-backend admin polling through
  cluster-specific URL patterns, cluster selector dropdown in the
  top bar, ?cluster=NAME on every /api/* endpoint to route to the
  right per-cluster Store.
- smartmet-webmon: Auto-detection — when no clusters.conf is
  present (or empty), smwebmon probes localhost for a SmartMet
  daemon, parses its clusterinfo to identify role (FRONTEND vs
  BACKEND), and on a frontend host derives a cluster definition
  from the local FQDN (`<prefix>.<cluster>.<domain>` → cluster
  name = `<cluster>`, admin-url-pattern =
  `http://{prefix}.<cluster>.<domain>:8081/admin`). On a backend
  host or any FQDN that doesn't match the convention, auto-detect
  quietly returns and single-host mode (existing 26.4 behaviour)
  takes over. --no-cluster-autodetect opts out.
- smartmet-webmon: /etc/smartmet-webmon/clusters.conf — INI-style
  file with one section per cluster (frontend-url,
  admin-url-pattern, optional log-glob / admin-interval /
  discovery-interval). Shipped with all cluster sections commented
  out, so a fresh install lands in auto-detect mode. Operators
  with non-FMI naming conventions uncomment + adjust.
  %config(noreplace), so site edits survive upgrades.
- smartmet-webmon: Two new endpoints exposed for the dashboard:
    /api/clusters             — list configured clusters with
                                discovery status (alive/total
                                backend counts).
    /api/cluster/topology     — per-cluster backend list with
                                handler service mix; powers the
                                planned topology card.
- smartmet-webmon: The same smartmet-webmon RPM works on backend
  and frontend hosts. Behaviour is configuration-driven: backend
  host = single-host mode (existing perf samplers + admin polling
  localhost); frontend host = cluster mode (admin polling N
  backends through the frontend's local clusterinfo). Both modes
  can co-exist when frontend is colocated with a backend's
  smartmetd.
- smartmet-webmon: Existing single-host functionality unchanged.
  With no clusters.conf and auto-detect off, smwebmon behaves
  exactly as in -11.

  Phase 2 (multi-line cluster panels — overlay one line per
  backend per panel) and Phase 3 (cluster topology card +
  aggregates) are queued; this release is the wiring layer.
  Existing per-host panels keep rendering all backends as separate
  rows when ?cluster= is in scope, which is functional but not
  the eventual cluster-friendly shape.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-11.fmi
- smartmet-webmon: Chart Y-axis nice-tick algorithm switched from
  qdstat-style (2/5/10 ladder) to Heckbert-style (1/2/5/10 ladder).
  The two algorithms agree on most inputs, but differ when vmax
  falls in the "needs a 1-step" range. Concrete examples where
  qdstat would produce coarser labels than the operator wants:
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
- smartmet-webmon: Y-axis labels on every chart now use "nice"
  tick boundaries (multiples of 1, 2, or 5 times a power of 10)
  instead of raw vmax values. JS port of qdtools/main/qdstat.cpp:autotick
  + autoscale (the FMI house algorithm for histogram bin
  boundaries), so qdstat and smwebmon round the same way. The
  user reported axis labels like 0, 0.81, 1.62, 2.42, 3.23 —
  those become 0, 1, 2, 3, 4. For larger ranges: 0, 2, 4, 6, 8
  or 0, 5, 10, 15 as the data demands. Tick count adapts to
  chart height (more ticks on tall charts, fewer on the small
  ones in the Proc / Network grids). Subtle horizontal grid
  lines at each interior tick.
- smartmet-webmon: Y-axis chart scale now uses niceMax instead
  of dataMax, so the topmost tick aligns with the chart's top
  edge instead of the line just barely touching the ceiling.
- smartmet-webmon: Format helpers (formatMs / formatBytes /
  formatCount) trim trailing-zero fractions so a nice-tick value
  of 1 renders as "1ms" not "1.0ms" and 4 as "4" not "4.0".

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
- smartmet-webmon: The unit now ships with
  AmbientCapabilities=CAP_SYS_PTRACE + CAP_SYS_ADMIN and
  NoNewPrivileges=no. Investigation on c3.back.smartmet.fmi.fi
  showed perf_event_open(2) was failing with EACCES even at
  kernel.perf_event_paranoid=-1 + SELinux Permissive +
  lockdown=none + same-uid as smartmetd (i.e. every obvious wall
  removed). Root cause: smartmetd is launched by the
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
- smartmet-webmon: Recommends kernel-devel-uname-r so the
  bcc-tools modes (off-CPU / biolat / runqlat) work without a
  separate operator step. Their failure mode was a chdir into
  /lib/modules/$(uname -r)/build/ which only exists when
  kernel-devel is installed. The kheaders module loaded at boot
  is a separate header source that bcc on RHEL 8 doesn't yet
  consume, so kheaders alone wasn't enough.
- smartmet-webmon: smwebmon now schedules sampler tasks BEFORE
  awaiting --replay. asyncio runs them in parallel with the
  replay's blocking await, so /api/flame/status reports real
  sampler state from second one instead of "(disabled)" for the
  duration of the replay scan (which can take minutes on a busy
  backend with default --history-minutes=1440 and 1 GiB
  --replay-bytes). Stderr also now logs "replay starting / done
  in Ns / scheduled N source tasks" so the journal shows what
  stage startup is in.
- smartmet-webmon: Web UI — per-chart mouse-over with vertical
  guide line, dot at the cursor's data point, and a floating
  tooltip showing HH:MM:SS plus the formatted value. The X-axis
  grew from two static labels to 3-5 evenly-spaced HH:MM ticks.
  Hover overlay survives 2-second poll repaints. Wired up for
  the Overview charts, the Active in-flight chart, the URLs
  detail-modal 60-min chart, and the Proc panel's RSS / IO /
  threads / majflt charts. (Network panel charts still need
  timestamp metadata in the snapshot — deferred to a follow-up.)
- smartmet-webmon: Web UI — Caches and Services panels' name
  column widened so long cache names / handler names display
  fully instead of collapsing to a few characters. The trailing
  trend sparkline now compresses to its 80 px minimum when the
  name needs the width, instead of greedily claiming 100 %% of
  the row.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-7.fmi
- New reference doc: /usr/share/doc/smartmet-monitor/perf-event-paranoid.md
  covering the kernel.perf_event_paranoid sysctl, what each level
  blocks, which monitor features need which level, the kheaders
  module gotcha for bcc-tools, and the alternative of granting the
  unit kernel capabilities. Linked from the README and the smwebmon
  man page.
- smartmet-webmon: The package now ships the kernel-side prereqs
  the dashboard needs to be useful: kernel.perf_event_paranoid = 0
  via /usr/lib/sysctl.d/99-smartmet-perf.conf and `kheaders` via
  /usr/lib/modules-load.d/smartmet-perf.conf, both applied at
  install time without requiring a reboot (sysctl --system +
  modprobe kheaders in %%post, with || : guards). Without these
  the Flame panel is reduced to "no samples" and the Proc panel's
  perfstat numbers show as zero — every operator who installed -1
  through -6 hit this. The package's audience is operators who
  run the dashboard as a deliberate decision; sites that can't
  accept paranoid=0 should use the smartmet-monitor CLI tools
  (smtop, bstat, bperf) instead and skip this package.
- smartmet-webmon: %%description rewritten to make these
  install-time host changes explicit and to point at
  perf-event-paranoid.md for the full reasoning.

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
- smartmet-webmon: Flame tab now shows the full multi-line perf
  stderr (and the per-mode status for off-CPU / page-fault /
  wakeup / blockflame / malloc / biolat / runqlat / perfstat)
  when a sampler fails. The panel header keeps showing the
  truncated one-line summary so it stays compact; below the
  breadcrumb a new "Sampler diagnostics" card surfaces every
  failing mode's full status text in monospace. The "Error:"
  first-line truncation is no longer the operator's only window
  into a perf failure — they see exactly what perf is complaining
  about.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-5.fmi
- smartmet-webmon: Default unit user changed from `smartmet` to
  `smartmet-server`. This is the user that owns smartmetd
  processes in production FMI deployments, and the kernel sysctl
  kernel.perf_event_paranoid=2 (the RHEL default) only allows
  perf record for processes the calling user owns. Profiling
  cross-uid was failing with exit=255 and a near-empty diagnostic
  even though paranoid=2 should "permit profiling" — the catch is
  the "your own processes" constraint. The unit now runs as the
  same user as smartmetd, so perf works out of the box. Group is
  intentionally unset so systemd derives it from the user's
  primary group in /etc/passwd (htj at FMI, smartmet-server
  elsewhere); use a drop-in if your site needs something different.
- smartmet-webmon: The %%pre useradd that created a `smartmet` user
  has been dropped. That user was a miscalibration in the original
  spec (-1 was based on an off-the-cuff name; production uses
  smartmet-server). Recommends: smartmet-server declares the actual
  dependency without making it a hard requirement (some sites
  build smartmet-server from source under different names).
- smartmet-webmon: Requires(pre): shadow-utils dropped — no
  scriptlet uses useradd any more.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-4.fmi
- smartmet-webmon: flame.js was not being shipped in the RPM
  payload. Same install bug class as the snapshots/ fix in -3:
  WEBMON_ASSETS was a hand-maintained list and flame.js (added
  26.4.28-2) had not been added to it. The browser fetched
  /static/flame.js, got 404, FlameView never loaded, and the
  Flame tab failed with "flame is undefined". The install-webmon
  target now auto-discovers files in share/smartmet/webmon/
  instead — same shape as the smartmet_top subpackage discovery
  so the same bug class can't recur.
- smartmet-webmon: The Flame-tab failure now surfaces the cause
  instead of the symptom: activatePanel() catches init errors,
  marks the panel inactive (so the polling loop stops re-throwing
  every 2 s), and shows a panel-empty card with the actual error
  and a hint to hard-refresh the cached static assets.
- smartmet-webmon: `--replay` now defaults ON for smwebmon (URLs
  panel comes up populated from log history at startup). Pass
  --no-replay to opt out — the sysconfig template documents both
  directions.
- smartmet-webmon: `--perf` now defaults ON for smwebmon, so the
  Flame tab works out of the box. Same scope as smtop --perf:
  on-CPU, off-CPU, page-fault, wakeup, block-I/O, plus biolat /
  runqlat / perfstat. --no-perf opts out. New flags
  --perf-interval, --perf-record-seconds and --malloc-flame
  mirror smtop. Requires perf installed and
  kernel.perf_event_paranoid <= 2 (the RHEL default).
- smartmet-webmon: Unit's CPUQuota raised from 50%% to 200%% so
  perf record / offcputime-bpfcc burst windows fit comfortably
  under the cgroup ceiling. MemoryMax stays at 512M. Both can be
  raised in a drop-in if a hot box wants more.

* Thu Apr 30 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 26.4.30-3.fmi
- Install rule was shipping a broken RPM. The new
  smartmet_top/snapshots/ subpackage (added in 26.4.28-1 to back the
  smwebmon JSON endpoints) was on disk in the source tree but was
  never listed in the Makefile's install target, so the RPM payload
  did not contain it. `make check` did not catch this because it
  imports from the source tree directly, not from %%{python3_sitelib}.
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
- `make check` (and therefore the RPM %%build phase) failed on the
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
  targets to stage the source tarball in rpm's %%_sourcedir once
  per make invocation, then call `rpmbuild -bb <spec>` per spec
  (the same pattern webmon-rpm was already using). `make rpms`
  now archives HEAD exactly once, runs both rpmbuild calls
  against the staged tarball, and produces both noarch RPMs.
- smartmet-webmon: Spec file lists the smwebmon(1) man page in
  %%files. The install-webmon Makefile target was already shipping
  it, but the spec missed it, so `make rpms` failed at the
  unpackaged-files check.

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
- smartmet-webmon: smwebmon auto-probes localhost:8080 (frontend)
  and localhost:8081 (backend) at startup when no -u is given,
  removing the need to configure /etc/sysconfig/smartmet-webmon
  at all on a typical SmartMet host. The unit can now be started
  directly after install with no edits. Pass --no-admin to disable.
- smartmet-webmon: Sysconfig template rewritten to reflect the
  auto-probe default; shows when overriding -u is actually needed
  (remote hosts or non-standard ports) rather than presenting it
  as required.

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
- smartmet-webmon: Full panel parity with smtop. Tab navigation
  across every smtop panel (Overview, Plugins, URLs, Caches,
  Services, Active, Keys, Proc, Network, Flame, Logs); each panel
  renders the same data smtop shows but with HTML Canvas charts
  replacing Braille sparklines, click-to-drill replacing keystroke
  navigation, and bookmarkable per-panel URLs (`/#/<panel>`).
  Highlights:
    * Overview: 5 full-width Canvas line charts over the retained
      history (req/min, mean ms, p95 ms, bytes/min, err %%).
    * Plugins: per-row latency + size sparklines, sortable
      by req/s / mean / p95 / err / bytes; window 60s..60m.
    * Caches: per-row hit-rate fill bar (color thresholds) plus
      hits/min trend sparkline; size cell coloured by fill ratio.
    * Services: per-row req/min sparkline; cpu%% column coloured
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
- smartmet-webmon: 24 JSON endpoints under /api/* (one per panel +
  chart/detail variants) plus /api/panels for client-side tab
  discovery. Every endpoint smoke-tested in `make check`.

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
- smartmet-webmon: Initial release. Browser dashboard companion to
  smartmet-monitor. Ships `smwebmon` (HTTP+JSON server) plus static
  assets; reuses the data-collection layer (Store, sources,
  snapshots) from smartmet-monitor at the exact-version level.
  URLs panel only in v1, with click-to-drill-down: per-window
  stats, latency histogram (HTML Canvas), status-code mix, top
  API keys, last-60-min mean latency line chart. systemd unit
  shipped disabled — start with `sudo systemctl start
  smartmet-webmon` when needed and SSH-tunnel to 127.0.0.1:8765.
  Runs as user `smartmet` so it reads the same access logs the
  daemon writes. No X11, no third-party Python deps.

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
  ≤ 2, PID exists; warns when > 50%% of frames are `[unknown]`
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
- Services panel gains a `cpu%%` column showing the fraction
  of avg_ms each handler spends ON CPU. Read from the
  AverageCPUMs field added in spine 26.4.27-2.fmi. Coloured
  by ratio: green ≥ 50%% (CPU-bound, on-CPU flame is the
  next stop), blue ≤ 10%% (wait-bound, off-CPU flame), neutral
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
  Colour bands: IPC < 0.3 / cache-miss > 30%% / branch-miss > 5%%
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
  represent (cache hit %%, share-of-load) read fine at cell-level.
- Graphs panel: tall layout. When there's enough room per plugin
  (≥ 3 rows), each plugin's row expands into a multi-row block:
  the name + numeric stats sit on the top row, two vertical Braille
  charts (response time + response size) span all `per_plugin` rows
  on the right. Each plugin's pattern is far more readable than
  the previous one-row-per-plugin layout. With many plugins (e.g.
  the Live composite's 22 sources) per_plugin drops to 1 and the
  panel falls back to the compact single-row layout.
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
  flamegraph while keeping the duty cycle at ~30%% (3s recording in
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
