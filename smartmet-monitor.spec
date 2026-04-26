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
Version:        26.4.26
Release:        5%{?dist}.fmi
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
%doc %{_docdir}/smartmet-monitor/images/
%{_python3_sitelib}/smartmet_top/

%changelog
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
