"""Process-level panel — memory + IO + (optional) perf for smartmetd PIDs.

Targets only `smartmetd` processes. If multiple are running (commonly
one backend + one frontend on the same host) the user cycles between
them with `n` (lowercase = within-panel; uppercase `N` is reserved
for switching to the Network panel) or jumps to a specific PID by
its per-row `[1]` / `[2]` / ... red mnemonic. Memory data comes from
cheap, O(1) /proc counters; the
expensive `smaps_rollup` is gated behind `r` so the operator never pays
for it without asking. The perf section is shown only when smtop was
launched with `--perf` and the operator opts in to the sampling load.
"""

from __future__ import annotations

import curses
import os
import time
from typing import Dict, List

from .. import theme
from ..snapshots.proc import ProcSnapshot
from ..sources.proc import read_smaps_rollup
from ..state.store import ProcInfo, ProcSample
from ..widgets.bars import human_bytes, human_count, sparkline, vchart
from .base import Panel, safe_addstr, write_label, write_row, write_section_header


def _fmt_us(microseconds: int) -> str:
    """Compact latency rendering, auto-scaling unit. Block I/O ranges
    from sub-microsecond (page cache hits) to seconds (queued I/O on
    saturated devices); printing everything as us blows up the column.
    """
    if microseconds <= 0:
        return "—"
    if microseconds < 1000:
        return f"{microseconds}us"
    if microseconds < 1_000_000:
        return f"{microseconds / 1000:.1f}ms"
    return f"{microseconds / 1_000_000:.2f}s"


def _humanize_kb(kb: int) -> str:
    return human_bytes(float(kb) * 1024.0)


def _format_uptime(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    if seconds < 86400:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    return f"{d}d {h}h"


def _io_rate(samples: List[ProcSample], field_name: str) -> float:
    if len(samples) < 2:
        return 0.0
    a, b = samples[-2], samples[-1]
    dt = max(0.001, b.ts - a.ts)
    return max(0.0, float(getattr(b, field_name) - getattr(a, field_name)) / dt)


def _majflt_rate(samples: List[ProcSample]) -> float:
    """Rate of major page faults per second, computed from the last
    pair of samples — same shape as `_io_rate` so the sparkline
    helpers can be re-used.
    """
    if len(samples) < 2:
        return 0.0
    a, b = samples[-2], samples[-1]
    dt = b.ts - a.ts
    if dt <= 0:
        return 0.0
    return max(0, b.majflt - a.majflt) / dt


def _majflt_rate_series(samples: List[ProcSample]) -> List[float]:
    if len(samples) < 2:
        return []
    out: List[float] = []
    for i in range(1, len(samples)):
        a, b = samples[i - 1], samples[i]
        dt = b.ts - a.ts
        if dt <= 0:
            out.append(0.0)
            continue
        out.append(max(0, b.majflt - a.majflt) / dt)
    return out


def _cpu_cores(samples: List[ProcSample]):
    """Return (user_cores, sys_cores) computed from the last sample
    pair. "Cores" here means "fraction of one CPU continuously busy"
    — 1.0 = one core fully utilised, 4.0 = four full cores worth of
    CPU. Computed as `Δ jiffies / SC_CLK_TCK / Δt`.

    Per-handler CPU is not yet available — that needs a spine-side
    change to expose CPU time alongside AverageDuration in
    ?what=servicestats. The value returned here is the host-side
    proxy: total smartmetd CPU usage. Useful for "did this deploy
    make the process more or less expensive overall?" even without
    the per-handler split.
    """
    if len(samples) < 2:
        return 0.0, 0.0
    a, b = samples[-2], samples[-1]
    dt = b.ts - a.ts
    if dt <= 0:
        return 0.0, 0.0
    try:
        clk = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        clk = 100
    if clk <= 0:
        clk = 100
    u = max(0, b.utime - a.utime) / clk / dt
    s = max(0, b.stime - a.stime) / clk / dt
    return u, s


def _io_rate_series(samples: List[ProcSample], field_name: str) -> List[float]:
    if len(samples) < 2:
        return []
    out: List[float] = []
    for i in range(1, len(samples)):
        dt = max(0.001, samples[i].ts - samples[i - 1].ts)
        d = getattr(samples[i], field_name) - getattr(samples[i - 1], field_name)
        out.append(max(0.0, d / dt))
    return out


# Stable per-symbol color: small palette indexed by hash of name.
_FLAME_COLORS = (
    theme.P_BAD,      # red
    theme.P_WARN,     # yellow
    theme.P_GOOD,     # green
    theme.P_SPARK,    # cyan
    theme.P_ACCENT,   # magenta
)


def _flame_color(sym: str) -> int:
    pair = _FLAME_COLORS[hash(sym) % len(_FLAME_COLORS)]
    return theme.attr(pair, curses.A_BOLD | curses.A_REVERSE)


# ---- shared PID selector ---------------------------------------------------

def draw_pid_selector(win, store, top: int, max_show: int = 9) -> int:
    """Render a numbered list of smartmetd PIDs starting at row `top`.

    Each row carries the index ([1]..[9] in red as the keyboard
    shortcut), the PID, the detected role, and the full cmdline so the
    operator can tell frontend from backend without having to know the
    port. The currently-selected PID is drawn with reverse video.
    Returns the first row index below the selector.
    """
    h, w = win.getmaxyx()
    procs = store.proc_list()
    if not procs:
        return top
    showing = procs[:max_show]
    selected = store.proc_selected()
    for i, info in enumerate(showing):
        y = top + i
        if y >= h - 1:
            break
        is_sel = (info.pid == selected)
        # Selected row: reverse video across the whole line so the
        # operator can see at a glance which process is being graphed.
        sel_attr = curses.A_REVERSE | curses.A_BOLD if is_sel else 0
        idx_attr = (curses.A_REVERSE | curses.A_BOLD if is_sel
                    else theme.attr(theme.P_MNEMONIC,
                                    curses.A_BOLD | curses.A_UNDERLINE))
        # [N]
        idx_str = f"[{i + 1}]"
        safe_addstr(win, y, 0, idx_str, idx_attr)
        # PID + role + cmdline, all in the row's base attribute so the
        # selected highlight covers the whole line uniformly.
        prefix = f" smartmetd[{info.pid}] {info.role:<9} "
        avail = max(0, w - len(idx_str) - len(prefix) - 1)
        cmdline = (info.cmdline or "")[:avail]
        rest = prefix + cmdline
        safe_addstr(win, y, len(idx_str), rest, sel_attr)
        # Pad selected row to end so the reverse-video bar runs full-width.
        used = len(idx_str) + len(rest)
        if is_sel and used < w - 1:
            safe_addstr(win, y, used, " " * (w - used - 1), sel_attr)
    return top + len(showing)


# ---- flamegraph tree builder + renderer ------------------------------------

def _build_flame_tree(stacks) -> Dict[str, list]:
    """{symbol: [count, children_dict]} — root → leaf accumulation.

    Each item in `stacks` may be either a bare frame tuple (counted as
    1 sample, the on-CPU case) or a `(frame_tuple, weight)` 2-tuple
    where the weight is added instead of 1 (the off-CPU case, where
    weight = microseconds spent blocked). The shape is detected per
    item — calling code does not need to know which branch will fire.
    """
    root: Dict[str, list] = {}
    for item in stacks:
        # (stack, weight) form: a 2-tuple whose first element is itself
        # a tuple of frames and second element a number. Anything else
        # is interpreted as a plain frame tuple.
        if (isinstance(item, tuple) and len(item) == 2
                and isinstance(item[0], tuple)
                and isinstance(item[1], (int, float))):
            stack, weight = item
        else:
            stack, weight = item, 1
        node = root
        for sym in stack:
            entry = node.get(sym)
            if entry is None:
                entry = [0, {}]
                node[sym] = entry
            entry[0] += weight
            node = entry[1]
    return root


def _render_flame_level(win, y: int, max_y: int, x: int, width: int,
                        nodes: Dict[str, list], parent_count: int) -> None:
    if y > max_y or width <= 0 or not nodes:
        return
    total = parent_count if parent_count > 0 else 1
    items = sorted(nodes.items(), key=lambda kv: -kv[1][0])
    cur_x = x
    remaining = width
    for sym, (cnt, children) in items:
        if remaining <= 0:
            break
        cw = max(1, int(round(cnt / total * width)))
        cw = min(cw, remaining)
        if cw < 1:
            continue
        if len(sym) > cw:
            label = sym[:cw]
        else:
            label = sym + " " * (cw - len(sym))
        safe_addstr(win, y, cur_x, label, _flame_color(sym))
        if children and y < max_y:
            _render_flame_level(win, y + 1, max_y, cur_x, cw, children, cnt or 1)
        cur_x += cw
        remaining -= cw


# ---- panel -----------------------------------------------------------------

class ProcPanel(Panel):
    name = "Proc"
    hotkey = "p"
    help_text = (
        "Per-process memory + I/O + host-wide reclaim, network and "
        "scheduler metrics (with --perf, also live perf-top + "
        "flamegraph). b/n cycle PID (or 1-9 by index), m/i/g toggle "
        "Memory/I/O/paGe-fault sections, r smaps_rollup, "
        "f flamegraph toggle, +/- adjust sparkline height."
    )
    panel_help = """\
Sections, top → bottom:

Memory:
  RSS    total resident-set size (file-mapped + anon + shmem).
  File   file-backed pages (memory-mapped QueryData files etc).
         A drop here during a fault storm = page-cache eviction.
  Anon   anonymous (heap) pages — what malloc returns from.
  Shmem  shared memory.
  VmSize total virtual address space; VmPTE page-table size;
  Swap   bytes swapped out (red when > 0 — production should
         never swap); HWM peak RSS since process start.

I/O:
  R / W   bytes/s read / written via /proc/PID/io. Includes
          page-cache reads — not exactly "disk traffic" but the
          best per-PID number /proc exposes.
  FDs     open file descriptors.

Page faults (major):
  Synchronous reads from disk because a page wasn't in RAM.
  Sustained > 100/s = working set exceeds page cache. Killer
  signal for SmartMet's mmapped QueryData. See the matching
  Page-fault flame mode (M) for "where in code".

Page cache + reclaim (host):
  cache  Cached + Buffers as % of MemTotal.
  sys-majflt    host-wide major-fault rate (complements the
                per-PID one above; catches faults from any
                process).
  kswapd        background reclaim — silent and healthy.
  direct        DIRECT reclaim — application thread had to
                free pages itself before its malloc could
                return. Sparklined in red because any
                positive sustained value is bad.

Block I/O latency (host):
  p50/p95/p99   from biolatency-bpfcc; weighted by IOPS.
  Multi-ms p95 = storage saturation.

Run-queue latency (host):
  p50/p95/p99 of "ready-but-not-running" wait time from
  runqlat-bpfcc. p95 ≥ 1 ms means the scheduler is the
  bottleneck — usually CFS quota throttling or noisy-neighbour
  VM. Critical on virtualised hosts.

CPU efficiency (perf stat):
  IPC          instructions per cycle for the focused PID.
               <0.3 = memory-stalled; ≥1.0 = healthy.
  cache-miss   LLC miss rate. >30% = working set exceeds L3.
  branch-miss  >5% = unpredictable hot-loop branches.

Network (host):
  Compact summary of TCP retransmits, listen drops + the
  busiest rx/tx NIC pair. The dedicated Network panel (n)
  carries the full detail.

Perf top / Flamegraph (with --perf):
  f toggles between perf-top symbol list and an inline
  flamegraph for the focused PID. Either way, the dedicated
  Flame view (mnemonic f at panel level) is a fuller renderer.

Keys:
  b / n      cycle selected smartmetd PID (back / next)
  1 - 9      jump to PID by index (red [N] mnemonic per row)
  m          toggle Memory section
  i          toggle I/O section
  g          toggle paGe-fault rate section
  N          switch to Network panel (uppercase = panel switch)
  r          read /proc/PID/smaps_rollup (expensive — runs
             only when pressed)
  f          toggle inline flame ↔ perf-top
  + / -      grow / shrink sparkline height (1-6 rows)
  e / E      export panel as CSV / JSON
"""

    # Sparkline height bounds. Default 2 elevates the data density of
    # the per-section rate and percentile graphs above the bottom-of-
    # panel perf-top symbols list, which carries less actionable
    # information for live operational diagnosis. Tunable via `+` /
    # `-` so an operator on a tall terminal can fit even more
    # vertical resolution.
    SPARK_H_MIN = 1
    SPARK_H_MAX = 6
    SPARK_H_DEFAULT = 2

    def __init__(self) -> None:
        self.rollup_msg = ""
        self.flame_view = False  # toggled by 'f'
        self._spark_h = self.SPARK_H_DEFAULT
        # Section visibility set. Lowercase letters (m/i/g) toggle
        # each of the always-on sections; the optional sampler-gated
        # sections (vmstats, biolat, runqlat, perfstat, netstats,
        # perf, rollup) appear only when their data exists, so a
        # toggle on top would be redundant — they hide naturally.
        self._visible = {"m", "i", "g"}

    # ---- key handling ------------------------------------------------------

    def handle_key(self, key, store):
        procs = store.proc_list()
        if not procs:
            return False
        pids = [p.pid for p in procs]
        selected = store.proc_selected()
        if selected is None or selected not in pids:
            selected = store.proc_default_pid() or pids[0]
            store.proc_select(selected)

        # Hotkey case convention: lowercase = within-panel,
        # uppercase = switch-panel. Lowercase ``b`` and ``n`` cycle
        # the selected smartmetd PID (back / next — the bottom-of-
        # panel ``< b PID n >`` widget mirrors this); uppercase
        # ``B`` and ``N`` are *not* consumed so the App-level
        # dispatcher can switch panels (only ``N`` matches a panel
        # currently — Network).
        if key == ord("n"):
            i = pids.index(selected)
            store.proc_select(pids[(i + 1) % len(pids)])
        elif key == ord("b"):
            i = pids.index(selected)
            store.proc_select(pids[(i - 1) % len(pids)])
        elif key in (ord("m"), ord("i"), ord("g")):
            # Section toggle: m = Memory, i = I/O, g = paGe-faults.
            # The visibility set is the source of truth for both the
            # renderer (skip drawing) and the section-header chevron.
            letter = chr(key)
            if letter in self._visible:
                self._visible.discard(letter)
            else:
                self._visible.add(letter)
        elif ord("1") <= key <= ord("9"):
            idx = key - ord("1")
            if idx < len(pids):
                store.proc_select(pids[idx])
            else:
                return False
        elif key == ord("r"):
            self._run_rollup(store)
        elif key == ord("f"):
            self.flame_view = not self.flame_view
        elif key in (ord("+"), ord("=")):
            # `=` is the same physical key as `+` without Shift — accept
            # both so the operator does not have to think about layout.
            self._spark_h = min(self.SPARK_H_MAX, self._spark_h + 1)
        elif key == ord("-"):
            self._spark_h = max(self.SPARK_H_MIN, self._spark_h - 1)
        else:
            return False
        return True

    def _draw_spark(self, win, y: int, x: int, values, attr: int,
                    width: int, maxval: float = 0.0) -> None:
        """Render a sparkline at (y, x), spanning `self._spark_h`
        rows downward when h > 1. Uses vchart() for the multi-row
        case so each rendered cell has 4 dot rows of vertical
        resolution; a height of 2 thus packs 8 levels into the
        section, four times what a single-row sparkline gives.
        """
        if not values:
            return
        h_avail = win.getmaxyx()[0] - y
        if h_avail < 1:
            return
        height = min(self._spark_h, h_avail)
        if height <= 1:
            safe_addstr(win, y, x, sparkline(values, width=width,
                                             maxval=maxval), attr)
            return
        rows = vchart(values, height=height, width=width, maxval=maxval)
        for j, line in enumerate(rows):
            if y + j >= win.getmaxyx()[0]:
                break
            safe_addstr(win, y + j, x, line, attr)

    def _run_rollup(self, store) -> None:
        pid = store.proc_selected()
        if pid is None:
            return
        try:
            roll = read_smaps_rollup(pid)
        except Exception as e:
            self.rollup_msg = f"rollup failed: {e}"
            return
        store.proc_set_rollup(pid, roll, time.time())
        self.rollup_msg = ""

    # ---- export ------------------------------------------------------------

    def export_snapshot(self, store):
        return ProcSnapshot.table(store)

    # ---- drawing -----------------------------------------------------------

    def draw(self, win, store):
        h, w = win.getmaxyx()
        procs = store.proc_list()

        if not procs:
            safe_addstr(win, 0, 0,
                        " Proc — no smartmetd processes found".ljust(w - 1),
                        theme.attr(theme.P_TAB_ACTIVE))
            safe_addstr(win, 2, 2,
                        "smartmetd is not running on this host, or this user "
                        "cannot read /proc/PID/status for it.",
                        theme.attr(theme.P_DIM))
            self._draw_footer(win, n_procs=0, perf_enabled=store.perf_enabled)
            return

        selected = store.proc_selected()
        if selected is None or selected not in [p.pid for p in procs]:
            selected = store.proc_default_pid() or procs[0].pid
            store.proc_select(selected)
        info = next((p for p in procs if p.pid == selected), procs[0])

        # PID list at the very top: numbered, with each cmdline visible
        # so frontend / backend / other can be picked apart at a glance.
        sel_bottom = draw_pid_selector(win, store, top=0)
        row = self._draw_header(win, info, len(procs), top=sel_bottom)
        row = self._draw_memory(win, info, row)
        row = self._draw_io(win, info, row)
        row = self._draw_page_faults(win, info, row)
        # Block-I/O latency is host-wide and only meaningful when the
        # biolat sampler ran at least once. Render only when enabled
        # (and when there's room) so unrelated hosts without bcc-tools
        # don't see a permanent "(no data)" line.
        if store.vmstats_enabled and store.vmstats_samples:
            row = self._draw_vmstats(win, store, row)
        if store.biolat_enabled and store.biolat_samples:
            row = self._draw_biolat(win, store, row)
        if store.runqlat_enabled and store.runqlat_samples:
            row = self._draw_runqlat(win, store, row)
        if store.perfstat_enabled and store.perfstat_samples:
            row = self._draw_perfstat(win, store, row)
        if store.netstats_enabled and store.netstats_tcp:
            row = self._draw_netstats(win, store, row)
        if store.perf_enabled:
            row = self._draw_perf(win, store, info, row)
        if info.rollup_ts > 0:
            row = self._draw_rollup(win, info, row)
        self._draw_footer(win, n_procs=len(procs), perf_enabled=store.perf_enabled)

    def _draw_header(self, win, info: ProcInfo, n_procs: int,
                     top: int = 0) -> int:
        """Status line for the focused PID. The PID list above already
        carries the cmdline + role, so this row is just live numbers
        (uptime, threads, CPU usage) that wouldn't fit there.
        """
        h, w = win.getmaxyx()
        latest = info.samples[-1] if info.samples else None
        threads = latest.threads if latest else 0
        uptime = (
            _format_uptime(time.time() - info.started_at)
            if info.started_at > 0 else "?"
        )
        cpu_user, cpu_sys = _cpu_cores(list(info.samples))
        cpu_total = cpu_user + cpu_sys
        cpu_str = (f"CPU {cpu_total:.2f}c (u{cpu_user:.2f} s{cpu_sys:.2f})"
                   if cpu_total > 0 else "CPU —")
        header = (
            f" Proc — pid={info.pid}  uptime={uptime}  "
            f"threads={threads}  {cpu_str}  "
            f"({n_procs} PID{'s' if n_procs != 1 else ''})"
        )
        safe_addstr(win, top, 0, header.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))
        return top + 2

    def _section_divider(self, win, y: int, label: str,
                          hotkey: str = "", hidden: bool = False) -> None:
        """Render the section header. If ``hotkey`` is given the divider
        carries a ``[k]`` red chip and a ▾/▸ chevron showing collapse
        state; otherwise it falls back to the plain ``─ Label ─`` form
        used by sections that aren't toggleable."""
        write_section_header(win, y, hotkey, label, hidden=hidden)

    def _draw_memory(self, win, info: ProcInfo, row: int) -> int:
        h, w = win.getmaxyx()
        if row >= h:
            return row
        hidden = "m" not in self._visible
        self._section_divider(win, row, "Memory", hotkey="m", hidden=hidden)
        row += 1
        # 4 stacked sparklines × spark_h rows each — bail early if the
        # section will not fit so we don't half-render. Hidden sections
        # fall through after rendering only the chevroned header.
        section_body_h = 4 * self._spark_h
        if hidden or row + section_body_h >= h:
            return row
        latest = info.samples[-1] if info.samples else ProcSample()
        rss_series = [s.vm_rss_kb / 1024.0 for s in info.samples]
        file_series = [s.rss_file_kb / 1024.0 for s in info.samples]
        anon_series = [s.rss_anon_kb / 1024.0 for s in info.samples]
        spark_w = max(20, min(40, w - 50))
        rows = [
            ("RSS",   latest.vm_rss_kb,    rss_series,  theme.P_SPARK),
            ("File",  latest.rss_file_kb,  file_series, theme.P_GOOD),
            ("Anon",  latest.rss_anon_kb,  anon_series, theme.P_WARN),
            ("Shmem", latest.rss_shmem_kb, None,        0),
        ]
        side_stats = [
            (f"VmSize {_humanize_kb(latest.vm_size_kb):>10}", 0),
            (f"VmPTE  {_humanize_kb(latest.vm_pte_kb):>10}", 0),
            (f"Swap   {_humanize_kb(latest.vm_swap_kb):>10}",
             theme.attr(theme.P_BAD, curses.A_BOLD) if latest.vm_swap_kb > 0 else 0),
            (f"HWM    {_humanize_kb(latest.vm_hwm_kb):>10}", 0),
        ]
        for i, (label, kb, series, color) in enumerate(rows):
            block_y = row + i * self._spark_h
            cells = [
                (f"  {label:<6} ", theme.attr(theme.P_HEADER)),
                (f"{_humanize_kb(kb):>10}  ", 0),
            ]
            x = write_row(win, block_y, 0, cells)
            if series is not None:
                self._draw_spark(win, block_y, x, series,
                                 theme.attr(color), spark_w)
                x += spark_w
            # Side stats land on the FIRST row of each block, next to
            # the labels — keeps them anchored to the metric they
            # belong to even when sparklines span several rows.
            sx = max(x + 2, w - 30)
            if sx < w - 2 and i < len(side_stats):
                text, attr = side_stats[i]
                safe_addstr(win, block_y, sx, text, attr)
        return row + 4 * self._spark_h

    def _draw_io(self, win, info: ProcInfo, row: int) -> int:
        h, w = win.getmaxyx()
        if row >= h:
            return row
        hidden = "i" not in self._visible
        self._section_divider(win, row, "I/O", hotkey="i", hidden=hidden)
        row += 1
        if hidden or row + self._spark_h >= h:
            return row
        latest = info.samples[-1] if info.samples else ProcSample()
        samples = list(info.samples)
        rrate = _io_rate(samples, "io_read_bytes")
        wrate = _io_rate(samples, "io_write_bytes")
        spark_w = max(15, min(30, (w - 60) // 2))

        cells = [
            (f"  R {human_bytes(rrate):>10}/s  ",
             theme.attr(theme.P_HEADER)),
        ]
        x = write_row(win, row, 0, cells)
        rs = _io_rate_series(samples, "io_read_bytes")
        self._draw_spark(win, row, x, rs, theme.attr(theme.P_SPARK), spark_w)
        x += spark_w + 2
        safe_addstr(win, row, x, f"W {human_bytes(wrate):>10}/s  ",
                    theme.attr(theme.P_HEADER))
        x += 18
        ws = _io_rate_series(samples, "io_write_bytes")
        self._draw_spark(win, row, x, ws, theme.attr(theme.P_SPARK), spark_w)
        x += spark_w + 2
        safe_addstr(win, row, x, f"FDs {human_count(latest.fds)}", 0)
        return row + self._spark_h + 1

    def _draw_page_faults(self, win, info: ProcInfo, row: int) -> int:
        """Major page-fault rate for this PID, with sparkline.

        Major faults indicate "the page wasn't in RAM and we had to
        read it from disk" — the canonical "fell out of page cache"
        signal. SmartMet mmaps QueryData files, so when a model run
        evicts the working set from page cache the next request that
        touches those pages incurs a wave of major faults and a
        latency spike that on-CPU profiling cannot see.
        """
        h, w = win.getmaxyx()
        if row >= h:
            return row
        hidden = "g" not in self._visible
        self._section_divider(win, row, "Page faults (major)",
                              hotkey="g", hidden=hidden)
        row += 1
        if hidden or row + self._spark_h >= h:
            return row
        samples = list(info.samples)
        rate = _majflt_rate(samples)
        spark_w = max(15, min(60, w - 50))
        # Colour the rate red when sustained — even a few hundred per
        # second is enough to dominate latency for a meteorology
        # workload, since each fault is a synchronous block read.
        rate_attr = (theme.attr(theme.P_BAD, curses.A_BOLD) if rate > 100
                     else theme.attr(theme.P_HEADER) if rate > 10
                     else theme.attr(theme.P_HEADER))
        cells = [
            (f"  rate {rate:>7.1f}/s  ", rate_attr),
            (f"total {samples[-1].majflt if samples else 0:>10}  ",
             theme.attr(theme.P_DIM)),
        ]
        x = write_row(win, row, 0, cells)
        series = _majflt_rate_series(samples)
        self._draw_spark(win, row, x, series, theme.attr(theme.P_SPARK),
                         spark_w)
        return row + self._spark_h + 1

    def _draw_perfstat(self, win, store, row: int) -> int:
        """CPU efficiency from perf stat: IPC + cache + branch miss rates.

        IPC (instructions per cycle) below 0.3 sustained is a strong
        memory-bound signal; cache-miss rate above ~30% says the
        working set is too big for the L2/L3 caches; branch-miss
        rate above ~5% says the hot loop has unpredictable control
        flow.
        """
        h, w = win.getmaxyx()
        if row + 1 + self._spark_h >= h:
            return row
        self._section_divider(win, row, "CPU efficiency (perf stat)")
        row += 1
        latest = (store.perfstat_samples[-1] if store.perfstat_samples
                  else None)
        if latest is None:
            safe_addstr(win, row, 2, "no samples yet — first cycle pending",
                        theme.attr(theme.P_DIM))
            return row + 2
        ts, pid, ipc, cm, bm = latest
        spark_w = max(15, min(60, w - 70))
        # IPC < 0.3 = memory bound; ≥ 1.0 = healthy. Colour by band.
        ipc_attr = (theme.attr(theme.P_BAD, curses.A_BOLD) if ipc < 0.3 and ipc > 0
                    else theme.attr(theme.P_WARN) if ipc < 0.6
                    else theme.attr(theme.P_GOOD)
                    if ipc >= 1.0 else theme.attr(theme.P_HEADER))
        cm_attr = (theme.attr(theme.P_BAD, curses.A_BOLD) if cm > 0.3
                   else theme.attr(theme.P_WARN) if cm > 0.1
                   else theme.attr(theme.P_HEADER))
        bm_attr = (theme.attr(theme.P_BAD, curses.A_BOLD) if bm > 0.05
                   else theme.attr(theme.P_HEADER))
        cells = [
            (f"  IPC {ipc:>4.2f}  ", ipc_attr),
            (f"cache-miss {cm*100:>5.1f}%  ", cm_attr),
            (f"branch-miss {bm*100:>4.1f}%  ", bm_attr),
            (f"pid={pid}  ", theme.attr(theme.P_DIM)),
        ]
        x = write_row(win, row, 0, cells)
        series = store.perfstat_ipc_series()
        self._draw_spark(win, row, x, series, theme.attr(theme.P_SPARK),
                         spark_w)
        return row + self._spark_h + 1

    def _draw_runqlat(self, win, store, row: int) -> int:
        """Run-queue latency: how long ready threads waited for CPU.

        On bare metal this should sit near zero. When it climbs, the
        kernel scheduler is the bottleneck — typical on virtualised
        / containerised hosts where CFS bandwidth controls or noisy
        neighbours hold ready threads off the run queue.
        """
        h, w = win.getmaxyx()
        if row + 1 + self._spark_h >= h:
            return row
        self._section_divider(win, row, "Run-queue latency (host)")
        row += 1
        latest = store.runqlat_samples[-1] if store.runqlat_samples else None
        if latest is None:
            safe_addstr(win, row, 2, "no samples yet — first cycle pending",
                        theme.attr(theme.P_DIM))
            return row + 2
        ts, p50, p95, p99, total = latest
        spark_w = max(15, min(60, w - 70))
        # Red on sustained ≥ 1 ms p95 (bare metal should be tens of µs).
        p95_attr = (theme.attr(theme.P_BAD, curses.A_BOLD) if p95 >= 1000
                    else theme.attr(theme.P_HEADER))
        cells = [
            (f"  p50 {_fmt_us(p50):>8}  ", theme.attr(theme.P_HEADER)),
            (f"p95 {_fmt_us(p95):>8}  ", p95_attr),
            (f"p99 {_fmt_us(p99):>8}  ", theme.attr(theme.P_HEADER)),
            (f"events {total:>6}  ", theme.attr(theme.P_ACCENT)),
        ]
        x = write_row(win, row, 0, cells)
        series = store.runqlat_p95_series()
        self._draw_spark(win, row, x, series, theme.attr(theme.P_SPARK),
                         spark_w)
        return row + self._spark_h + 1

    def _draw_netstats(self, win, store, row: int) -> int:
        """Network section: TCP retransmits / listen drops + per-NIC bandwidth.

        Host-wide; counters come from /proc/net/{snmp,netstat,dev}.
        Loopback is skipped at the source; remaining NICs each get
        one rx + one tx sparkline.
        """
        h, w = win.getmaxyx()
        if row + 4 >= h:
            return row
        self._section_divider(win, row, "Network (host)")
        row += 1
        retrans, overflows, drops = store.netstats_tcp_series()
        latest_r = retrans[-1] if retrans else 0.0
        latest_o = overflows[-1] if overflows else 0.0
        latest_d = drops[-1] if drops else 0.0
        spark_w = max(15, min(40, w - 60))
        # Bold red on retransmits sustained > 1 / s (anything more is
        # a clear network or peer problem) and on listen drops at all
        # (the application is failing to accept new connections).
        retrans_attr = (theme.attr(theme.P_BAD, curses.A_BOLD) if latest_r > 1
                        else theme.attr(theme.P_HEADER))
        drop_attr = (theme.attr(theme.P_BAD, curses.A_BOLD)
                     if (latest_o + latest_d) > 0
                     else theme.attr(theme.P_HEADER))
        cells = [
            (f"  retrans/s {latest_r:>6.1f}  ", retrans_attr),
            (f"listen-overflow/s {latest_o:>5.1f}  ", drop_attr),
            (f"listen-drop/s {latest_d:>5.1f}  ", drop_attr),
        ]
        x = write_row(win, row, 0, cells)
        self._draw_spark(win, row, x, retrans, theme.attr(theme.P_SPARK),
                         spark_w)
        row += self._spark_h
        # Per-interface rx/tx. To keep the panel scannable on hosts
        # with many NICs (think bonded interfaces + VLANs + tap
        # devices on a shared backend), auto-select the busiest
        # interface for INCOMING traffic and the busiest for
        # OUTGOING traffic over a recent window. They are usually
        # the same NIC; when they differ (e.g. NFS-bound rx on a
        # storage VLAN, public tx on a separate uplink) we render
        # both. Interface names are always shown so the operator
        # can see which NIC is being graphed.
        ifaces = store.netstats_iface_names()
        if ifaces:
            picks = self._pick_busiest_ifaces(store, ifaces)
            for label, iface in picks:
                if row + self._spark_h >= h - 1:
                    break
                self._draw_iface_row(win, store, row, label, iface, spark_w)
                row += self._spark_h
        return row + 1

    @staticmethod
    def _pick_busiest_ifaces(store, ifaces):
        """Return [(label, iface), …] picking the busiest rx and tx
        interfaces over the last ~12 samples (a minute at the default
        5 s netstats cycle).

        If the busiest rx and tx are the same NIC — typical on a
        single-uplink server — we return one entry labeled "busiest".
        If they diverge (the textbook case is a storage VLAN
        carrying rx and a public uplink carrying tx), we return two
        rows so neither is hidden.
        """
        WINDOW = 12

        def avg_tail(seq):
            if not seq:
                return 0.0
            tail = seq[-WINDOW:]
            return sum(tail) / len(tail)

        rx_max = (-1.0, ifaces[0])
        tx_max = (-1.0, ifaces[0])
        for iface in ifaces:
            rx, tx = store.netstats_iface_series(iface)
            r, t = avg_tail(rx), avg_tail(tx)
            if r > rx_max[0]:
                rx_max = (r, iface)
            if t > tx_max[0]:
                tx_max = (t, iface)
        if rx_max[1] == tx_max[1]:
            return [("busiest", rx_max[1])]
        return [
            ("rx-busy", rx_max[1]),
            ("tx-busy", tx_max[1]),
        ]

    def _draw_iface_row(self, win, store, row: int, label: str,
                         iface: str, spark_w: int) -> None:
        h, w = win.getmaxyx()
        rx, tx = store.netstats_iface_series(iface)
        rx_now = rx[-1] if rx else 0.0
        tx_now = tx[-1] if tx else 0.0
        cells = [
            (f"  {label:<7} ", theme.attr(theme.P_DIM)),
            (f"{iface:<8} ", theme.attr(theme.P_HEADER)),
            (f"rx {human_bytes(rx_now):>10}/s  ", 0),
        ]
        x = write_row(win, row, 0, cells)
        self._draw_spark(win, row, x, rx, theme.attr(theme.P_SPARK), spark_w)
        x += spark_w + 2
        safe_addstr(win, row, x, f"tx {human_bytes(tx_now):>10}/s  ",
                    theme.attr(theme.P_HEADER))
        x += 18
        if x + spark_w < w:
            self._draw_spark(win, row, x, tx, theme.attr(theme.P_SPARK),
                             spark_w)

    def _draw_vmstats(self, win, store, row: int) -> int:
        """Page-cache size + reclaim rates + system-wide major faults.

        The killer signal is direct reclaim — any sustained positive
        rate means alloc latency is leaking into request latency.
        kswapd reclaim is silent and healthy; we still show it so
        the operator can see the kernel doing its job.
        """
        h, w = win.getmaxyx()
        if row + 1 + self._spark_h >= h:
            return row
        self._section_divider(win, row, "Page cache + reclaim (host)")
        row += 1
        latest = store.vmstats_samples[-1]
        ts, majflt, kswapd, direct, scan, cache_kb, total_kb, avail_kb = latest
        cache_pct = (cache_kb / total_kb * 100) if total_kb > 0 else 0
        # Direct reclaim coloured red on any positive rate — it is
        # never healthy. kswapd at any rate is fine; we display it
        # in dim so the eye does not jump.
        direct_attr = (theme.attr(theme.P_BAD, curses.A_BOLD) if direct > 0
                       else theme.attr(theme.P_HEADER))
        cells = [
            (f"  cache {cache_kb // 1024:>6}MB "
             f"({cache_pct:>4.1f}% of {total_kb // 1024}MB)  ",
             theme.attr(theme.P_HEADER)),
            (f"sys-majflt {majflt:>5.1f}/s  ", theme.attr(theme.P_HEADER)),
            (f"kswapd {kswapd:>6.0f}/s  ", theme.attr(theme.P_DIM)),
            (f"direct {direct:>6.0f}/s  ", direct_attr),
        ]
        x = write_row(win, row, 0, cells)
        # Sparkline tracks DIRECT reclaim — that is the variable the
        # operator should watch for spikes. kswapd is steady-state
        # noise; sparklining it would hide the signal.
        spark_w = max(15, min(40, w - x - 2))
        series = store.vmstats_direct_series()
        if series and any(s > 0 for s in series):
            self._draw_spark(win, row, x, series, theme.attr(theme.P_BAD),
                             spark_w)
        return row + self._spark_h + 1

    def _draw_biolat(self, win, store, row: int) -> int:
        """Host-wide block-I/O latency from biolatency-bpfcc.

        Two rows:
          1. percentiles (p50 / p95 / p99) + IOPS, with a sparkline of p95
          2. (left blank — kept simple; future home for read/write split)
        """
        h, w = win.getmaxyx()
        if row + 1 + self._spark_h >= h:
            return row
        self._section_divider(win, row, "Block I/O latency (host)")
        row += 1
        latest = store.biolat_samples[-1] if store.biolat_samples else None
        if latest is None:
            safe_addstr(win, row, 2, "no samples yet — first cycle pending",
                        theme.attr(theme.P_DIM))
            return row + 2
        ts, p50, p95, p99, total = latest
        spark_w = max(15, min(60, w - 70))
        # Fixed-width formatter: latencies stretch from sub-microsecond
        # (cache hits) to seconds (failed/queued I/O). Auto-scale to
        # "us" / "ms" so the columns stay narrow.
        cells = [
            (f"  p50 {_fmt_us(p50):>8}  ", theme.attr(theme.P_HEADER)),
            (f"p95 {_fmt_us(p95):>8}  ", theme.attr(theme.P_HEADER)),
            (f"p99 {_fmt_us(p99):>8}  ", theme.attr(theme.P_HEADER)),
            (f"iops {total:>5}  ", theme.attr(theme.P_ACCENT)),
        ]
        x = write_row(win, row, 0, cells)
        series = store.biolat_p95_series()
        self._draw_spark(win, row, x, series, theme.attr(theme.P_SPARK),
                         spark_w)
        return row + self._spark_h + 1

    def _draw_perf(self, win, store, info: ProcInfo, row: int) -> int:
        h, w = win.getmaxyx()
        if row + 2 >= h:
            return row
        title = (
            "Flamegraph (live)" if self.flame_view else "Perf top symbols"
        )
        status = store.perf_status
        sample_count = store.perf_last_sample_count(info.pid)
        full_label = f"{title}  status={status}  last={sample_count}"
        self._section_divider(win, row, full_label)
        row += 1

        if self.flame_view:
            return self._draw_flame(win, store, info, row)
        return self._draw_perf_top(win, store, info, row)

    def _draw_perf_top(self, win, store, info: ProcInfo, row: int) -> int:
        h, w = win.getmaxyx()
        avail = h - row - 2  # leave space for footer + bottom divider
        if avail < 1:
            return row
        if store.perf_last_error:
            return self._draw_perf_error(win, row, avail, store.perf_last_error)
        rows = store.perf_top_symbols(info.pid, minutes=10, n=avail)
        if not rows:
            safe_addstr(win, row, 2,
                        "no samples yet — first cycle takes ~"
                        f"{int(store.perf_status and 1) + 1}s",
                        theme.attr(theme.P_DIM))
            return row + 1

        total = sum(c for _, c in rows) or 1
        spark_w = max(20, min(60, w - 60))
        for i, (sym, cnt) in enumerate(rows):
            y = row + i
            if y >= h - 1:
                break
            pct = cnt / total * 100
            series = store.perf_symbol_series(info.pid, sym, minutes=20)
            label = sym[:max(0, w - spark_w - 18)]
            cells = [
                (f"  {pct:>5.1f}%  ", theme.attr(theme.P_HEADER)),
                (f"{label:<{max(20, w - spark_w - 16)}}  ", 0),
            ]
            x = write_row(win, y, 0, cells)
            if series:
                safe_addstr(win, y, x, sparkline(series, width=spark_w),
                            theme.attr(theme.P_SPARK))
        return row + len(rows)

    def _draw_flame(self, win, store, info: ProcInfo, row: int) -> int:
        h, w = win.getmaxyx()
        avail = h - row - 2
        if avail < 1:
            return row
        if store.perf_last_error:
            return self._draw_perf_error(win, row, avail, store.perf_last_error)
        stacks = store.perf_recent_stacks(info.pid)
        if not stacks:
            safe_addstr(win, row, 2,
                        "no stack samples yet — waiting for first perf cycle…",
                        theme.attr(theme.P_DIM))
            return row + 1
        # Use the entire retained ring (already bounded at 20000 by the
        # store) so the tree is dense even when individual cycles are
        # short — the operator can switch to the dedicated Flame view
        # for a higher-fidelity rendering anyway.
        tree = _build_flame_tree(stacks)
        if not tree:
            return row + 1
        total = sum(v[0] for v in tree.values()) or 1
        max_y = row + avail - 1
        _render_flame_level(win, row, max_y, 0, w - 1, tree, total)
        return max_y + 1

    def _draw_perf_error(self, win, row: int, avail: int, msg: str) -> int:
        """Render a multi-line perf error block from the last failed cycle.

        Each line of `msg` (perf's stderr/stdout) goes on its own row up
        to the available height, prefixed with a small "│" so the block
        is visually distinct from the surrounding panel headers.
        """
        h, w = win.getmaxyx()
        bar = theme.attr(theme.P_BAD, curses.A_BOLD)
        lines = msg.splitlines() or [msg]
        # Always show the header marker even if the message is empty.
        safe_addstr(win, row, 2,
                    "perf cycle failed — showing the diagnostic in full:",
                    bar)
        used = 1
        for line in lines:
            if used >= avail:
                break
            safe_addstr(win, row + used, 4, line[:max(0, w - 6)],
                        theme.attr(theme.P_BAD))
            used += 1
        return row + used

    def _draw_rollup(self, win, info: ProcInfo, row: int) -> int:
        h, w = win.getmaxyx()
        if row + 2 >= h:
            return row
        title = (
            f"smaps_rollup  (last fetched: "
            f"{time.strftime('%H:%M:%S', time.localtime(info.rollup_ts))})"
        )
        self._section_divider(win, row, title)
        row += 1
        keys = [
            ("Pss", "Pss"),
            ("Pss_Anon", "Pss anon"),
            ("Pss_File", "Pss file"),
            ("Pss_Shmem", "Pss shmem"),
            ("Private_Dirty", "private dirty"),
            ("Shared_Dirty", "shared dirty"),
            ("Swap", "swap"),
            ("SwapPss", "swap pss"),
        ]
        for i, (k, label) in enumerate(keys):
            y = row + (i // 2)
            if y >= h - 1:
                break
            col = 2 if (i % 2 == 0) else max(40, w // 2)
            v = info.rollup.get(k, 0)
            safe_addstr(win, y, col,
                        f"{label:<14} {_humanize_kb(v):>10}",
                        theme.attr(theme.P_DIM) if v == 0 else 0)
        return row + (len(keys) + 1) // 2

    def _draw_footer(self, win, n_procs: int, perf_enabled: bool) -> None:
        h, w = win.getmaxyx()
        if h < 2:
            return
        hot = theme.attr(theme.P_MNEMONIC, curses.A_BOLD | curses.A_UNDERLINE)
        base = theme.attr(theme.P_TITLE)
        x = 0
        safe_addstr(win, h - 1, 0, " ", base); x += 1
        # Paired-cycle widget: ``< b PID n >``. ``b`` cycles the
        # selected PID backward, ``n`` forward. The angle brackets
        # are visual arrow cues; the noun ``PID`` is the thing being
        # navigated. Uppercase B / N fall through to global panel-
        # switch dispatch (only N matches a panel — Network).
        if n_procs > 1:
            safe_addstr(win, h - 1, x, "< ", base); x += 2
            x = write_label(win, h - 1, x, "b", 0, base, hot)
            safe_addstr(win, h - 1, x, " PID ", base); x += 5
            x = write_label(win, h - 1, x, "n", 0, base, hot)
            safe_addstr(win, h - 1, x, " >   ", base); x += 5
        # Section toggles, each rendered as a ``[k]`` chip whose
        # colour reflects the visibility state — red+bold when the
        # section is showing, dim when it's hidden. Mirrors the
        # convention used in the Network panel's footer.
        safe_addstr(win, h - 1, x, "toggle ", base); x += 7
        for letter, _label in (("m", "Memory"), ("i", "I/O"),
                                 ("g", "Page-faults")):
            on = letter in self._visible
            chip_attr = (theme.attr(theme.P_MNEMONIC, curses.A_BOLD)
                         if on else theme.attr(theme.P_DIM))
            safe_addstr(win, h - 1, x, f"[{letter}]", chip_attr); x += 3
            safe_addstr(win, h - 1, x, " ", base); x += 1
        safe_addstr(win, h - 1, x, "  ", base); x += 2
        x = write_label(win, h - 1, x, "r", 0, base, hot)
        x = write_label(win, h - 1, x, "ollup   ", 0, base, base)
        if perf_enabled:
            x = write_label(win, h - 1, x, "f", 0, base, hot)
            x = write_label(win, h - 1, x, "lame   ", 0, base, base)
        x = write_label(win, h - 1, x, "+", 0, base, hot)
        x = write_label(win, h - 1, x, "/", 0, base, base)
        x = write_label(win, h - 1, x, "-", 0, base, hot)
        x = write_label(win, h - 1, x, f" spark h={self._spark_h}   ",
                        0, base, base)
        x = write_label(win, h - 1, x, "e", 0, base, hot)
        x = write_label(win, h - 1, x, "/", 0, base, base)
        x = write_label(win, h - 1, x, "E", 0, base, hot)
        x = write_label(win, h - 1, x, " export", 0, base, base)
        if x < w - 1:
            safe_addstr(win, h - 1, x, " " * (w - x - 1), base)
