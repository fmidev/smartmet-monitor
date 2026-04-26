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
Version:        0.6.0
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
