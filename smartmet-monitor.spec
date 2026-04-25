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
Version:        0.3.1
Release:        1%{?dist}
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
%{_mandir}/man1/smtop.1*
%{_mandir}/man1/bstat.1*
%{_mandir}/man1/bchart.1*
%{_mandir}/man1/burls.1*
%{_mandir}/man1/bstatus.1*
%{_mandir}/man1/bkeys.1*
%{_mandir}/man1/bstat1s.1*
%{_mandir}/man1/bstat10s.1*
%{_mandir}/man1/bstat1.1*
%{_mandir}/man1/bstat10.1*
%{_mandir}/man1/bstat60.1*
%{_mandir}/man1/bstat24.1*
%doc %{_docdir}/smartmet-monitor/README.md
%{_python3_sitelib}/smartmet_top/

%changelog
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
