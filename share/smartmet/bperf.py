#!/usr/bin/env python3
"""bperf — record perf, filter to SmartMet, render flamegraph + call graph.

This is the offline companion to smtop's live Flame view. It runs
`perf record -F 99 -g --call-graph=dwarf,32768 -p PID -- sleep N`,
post-processes the captured stacks through the same SmartMet-only and
request-vs-background filters smtop uses, and writes three artifacts to
disk:

  * folded.txt  — Brendan-Gregg folded-stack format (one line per
                  unique stack, "frame1;frame2;… count"). Useful as
                  input to any other flamegraph viewer.
  * graph.dot   — GraphViz call graph; node weight is the cumulative
                  sample count, edge weight is samples that crossed
                  that caller→callee boundary. Render with:
                      dot -Tsvg graph.dot > graph.svg
  * flame.svg   — self-contained interactive flamegraph (click any
                  frame to zoom; opens in any browser, no external
                  JavaScript / fonts).

Pure stdlib only — same constraint as the rest of smartmet-monitor.
The SVG is rendered by hand so we don't pull in `flamegraph.pl` or
`inferno` as runtime dependencies.

Pre-flight: `perf` must be in PATH and the kernel must allow user-space
profiling (kernel.perf_event_paranoid <= 2 with -p, <= 1 without).
We surface the relevant install / sysctl hints when the probe fails.

The library lookup the wrapper uses (`SMARTMET_MONITOR_LIB`) is the
same convention as the bstat family, so running from the source tree
just works without environment juggling:

    bin/bperf -p $(pgrep -x smartmetd) -s 30 -o /tmp/bperf-out
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple


# When invoked via the wrapper, `share/smartmet/bperf.py` is alongside
# the installed smartmet_top package on the Python path. When invoked
# from the source tree, the wrapper exports PYTHONPATH so the `..`
# import below resolves to monitor/smartmet_top/. Either way this is a
# normal `from smartmet_top.sources.smartmet_filter import …`.
from smartmet_top.sources.smartmet_filter import (
    THREAD_CLASS_ALL,
    THREAD_CLASS_BACKGROUND,
    THREAD_CLASS_REQUEST,
    THREAD_CLASSES,
    collapse_to_smartmet,
    is_request_stack,
    is_smartmet_frame,
    keep_for_thread_class,
)


PERF_FREQ_DEFAULT = 99
DEFAULT_SECONDS = 30
DEFAULT_OUT_DIR = "."

# Same regex perftop.py uses — keep them in sync if either side changes.
_FRAME_RE = re.compile(
    r"^\s+[0-9a-fA-F]+\s+(?P<sym>.+?)\s+\((?P<dso>[^()]*)\)\s*$"
)
_OFFSET_RE = re.compile(r"\+0x[0-9a-fA-F]+$")


def _strip_offset(symbol: str) -> str:
    return _OFFSET_RE.sub("", symbol).strip() or "[unknown]"


# ---------------------------------------------------------------------------
# pre-flight
# ---------------------------------------------------------------------------

def _read_paranoid() -> Optional[int]:
    try:
        with open("/proc/sys/kernel/perf_event_paranoid", "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _resolve_pid(arg: Optional[str]) -> int:
    """Resolve --pid: a literal PID, a pgrep query, or auto-detect smartmetd."""
    if arg and arg.isdigit():
        return int(arg)
    target = arg or "smartmetd"
    out = subprocess.run(
        ["pgrep", "-x", target],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, check=False,
    )
    pids = [int(p) for p in out.stdout.split() if p.strip().isdigit()]
    if not pids:
        sys.exit(
            f"bperf: no process named {target!r} found (try -p PID, "
            f"or `pgrep -af smartmetd` to see what's running)"
        )
    if len(pids) > 1:
        sys.exit(
            f"bperf: multiple {target!r} processes found ({pids}); "
            f"pick one with -p PID"
        )
    return pids[0]


def _preflight(pid: int) -> None:
    """Surface install / sysctl hints before we burn 30s of perf overhead."""
    if shutil.which("perf") is None:
        sys.exit(
            "bperf: `perf` is not in PATH. Install linux-tools "
            "(RHEL/Fedora: dnf install perf; Debian/Ubuntu: apt "
            "install linux-tools-generic) and re-run."
        )
    paranoid = _read_paranoid()
    if paranoid is not None and paranoid > 2 and os.geteuid() != 0:
        sys.exit(
            f"bperf: kernel.perf_event_paranoid is {paranoid}, which "
            f"forbids user-space profiling. Either run bperf as root "
            f"(`sudo bperf …`) or lower the sysctl with "
            f"`sudo sysctl kernel.perf_event_paranoid=1`."
        )
    # Confirm the PID actually exists and we can see its thread list —
    # gives a precise error rather than a perf-record diagnostic.
    if not os.path.isdir(f"/proc/{pid}"):
        sys.exit(f"bperf: PID {pid} does not exist.")


# ---------------------------------------------------------------------------
# capture + parse
# ---------------------------------------------------------------------------

def _run_perf_record(pid: int, seconds: int, out_path: str,
                     freq: int) -> None:
    cmd = [
        "perf", "record",
        "-F", str(freq),
        "--call-graph=dwarf,32768",
        "-p", str(pid),
        "-o", out_path,
        "--", "sleep", str(seconds),
    ]
    print(f"bperf: capturing {seconds}s @ {freq}Hz from pid={pid} …",
          file=sys.stderr)
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        sys.exit(f"bperf: perf record exited with status {rc}")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        sys.exit("bperf: perf produced no data (PID may have exited)")


def _run_perf_script(perf_data: str) -> str:
    out = subprocess.run(
        ["perf", "script", "-i", perf_data, "--no-inline"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, check=False,
    )
    if out.returncode != 0:
        sys.exit(f"bperf: perf script exited with status {out.returncode}")
    return out.stdout


def parse_perf_script(text: str) -> List[Tuple[str, ...]]:
    """Mirror of perftop.parse_perf_script. Stacks come out root → leaf."""
    stacks: List[Tuple[str, ...]] = []
    current: List[str] = []
    in_sample = False
    for raw in text.splitlines():
        if not raw.strip():
            if current:
                stacks.append(tuple(reversed(current)))
                current = []
            in_sample = False
            continue
        if not in_sample:
            in_sample = True
            continue
        m = _FRAME_RE.match(raw)
        if not m:
            continue
        current.append(_strip_offset(m.group("sym")))
    if current:
        stacks.append(tuple(reversed(current)))
    return stacks


# ---------------------------------------------------------------------------
# filtering + aggregation
# ---------------------------------------------------------------------------

def filter_stacks(
    stacks: List[Tuple[str, ...]],
    thread_class: str,
    smartmet_only: bool,
) -> List[Tuple[str, ...]]:
    out: List[Tuple[str, ...]] = []
    for st in stacks:
        if not keep_for_thread_class(st, thread_class):
            continue
        if smartmet_only:
            collapsed = collapse_to_smartmet(st)
            if collapsed is None:
                continue
            out.append(collapsed)
        else:
            out.append(st)
    return out


def fold_stacks(stacks: List[Tuple[str, ...]]) -> Dict[Tuple[str, ...], int]:
    folded: Dict[Tuple[str, ...], int] = defaultdict(int)
    for st in stacks:
        if st:
            folded[st] += 1
    return folded


def estimate_unknown_ratio(stacks: List[Tuple[str, ...]]) -> float:
    """Fraction of frames that are `[unknown]` — proxy for missing
    debuginfo. Returns 0.0 when the input is empty."""
    total = 0
    unknown = 0
    for st in stacks:
        for f in st:
            total += 1
            if f == "[unknown]":
                unknown += 1
    return unknown / total if total else 0.0


# ---------------------------------------------------------------------------
# output: folded text
# ---------------------------------------------------------------------------

def write_folded(folded: Dict[Tuple[str, ...], int], path: str) -> None:
    with open(path, "w") as f:
        for stack, count in sorted(folded.items(), key=lambda kv: -kv[1]):
            f.write(";".join(stack) + " " + str(count) + "\n")


# ---------------------------------------------------------------------------
# output: GraphViz .dot
# ---------------------------------------------------------------------------

def write_dot(folded: Dict[Tuple[str, ...], int], path: str,
              label: str) -> None:
    """Aggregate folded stacks into a call graph and write GraphViz dot.

    Node weight = inclusive samples (count of stacks passing through).
    Edge weight = samples that crossed the caller→callee boundary.
    Pen width and node fill are scaled by weight, capped so a single
    hot path doesn't overwhelm the layout.
    """
    node_weight: Dict[str, int] = defaultdict(int)
    edge_weight: Dict[Tuple[str, str], int] = defaultdict(int)
    for stack, count in folded.items():
        for sym in stack:
            node_weight[sym] += count
        for caller, callee in zip(stack, stack[1:]):
            edge_weight[(caller, callee)] += count
    if not node_weight:
        # Empty graph — still write a placeholder so the operator gets
        # a file at the documented path rather than a missing-file
        # error from `dot`.
        with open(path, "w") as f:
            f.write('digraph bperf {\n  label="no samples"\n}\n')
        return
    max_n = max(node_weight.values()) or 1
    max_e = max(edge_weight.values()) or 1
    with open(path, "w") as f:
        f.write("digraph bperf {\n")
        f.write(f'  label="{label}"\n')
        f.write('  rankdir=TB\n')
        f.write('  node [shape=box, style=filled, fontname="Helvetica"]\n')
        f.write('  edge [fontname="Helvetica", fontsize=9]\n')
        for i, (sym, w) in enumerate(
                sorted(node_weight.items(), key=lambda kv: -kv[1])):
            # Map weight to a yellow→red gradient. Pure HSV is overkill;
            # a simple lerp on the V channel of #FFE0E0 → #B22222 reads
            # well in print and is colour-blind friendly enough.
            t = w / max_n
            r = int(255 - (255 - 178) * t)
            g = int(224 - (224 - 34) * t)
            b = int(224 - (224 - 34) * t)
            colour = f"#{r:02x}{g:02x}{b:02x}"
            short = _short_label(sym)
            pct = w / sum(node_weight.values()) * 100
            f.write(
                f'  n{i} [label="{html.escape(short)}\\n'
                f'{w} ({pct:.1f}%)", '
                f'fillcolor="{colour}"]\n'
            )
        # Resolve symbol → node id for the edge pass.
        sym_id: Dict[str, int] = {}
        for i, (sym, _w) in enumerate(
                sorted(node_weight.items(), key=lambda kv: -kv[1])):
            sym_id[sym] = i
        for (caller, callee), w in sorted(
                edge_weight.items(), key=lambda kv: -kv[1]):
            pen = 1 + 6 * (w / max_e)
            f.write(
                f'  n{sym_id[caller]} -> n{sym_id[callee]} '
                f'[penwidth={pen:.2f}, label="{w}"]\n'
            )
        f.write("}\n")


def _short_label(sym: str, max_len: int = 60) -> str:
    """Shorten C++ template / namespace blowups for graph readability."""
    if len(sym) <= max_len:
        return sym
    # Strip the SmartMet:: prefix when present — it's the same on every
    # node in a SmartMet-only filter and just eats horizontal space.
    if sym.startswith("SmartMet::"):
        sym = sym[len("SmartMet::"):]
    if len(sym) <= max_len:
        return sym
    return sym[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# output: SVG flamegraph (self-contained, click-to-zoom)
# ---------------------------------------------------------------------------

# Layout constants. Picked to match the proportions of Brendan Gregg's
# original FlameGraph output so seasoned operators don't have to re-
# learn how to read the file.
SVG_WIDTH = 1200
SVG_FRAME_HEIGHT = 16
SVG_TOP_PAD = 24
SVG_FONT_SIZE = 11
SVG_MIN_FRAME_PIXELS = 0.1  # frames narrower than this are dropped


def write_svg(folded: Dict[Tuple[str, ...], int], path: str,
              title: str) -> None:
    """Render a self-contained, click-to-zoom flamegraph SVG.

    The interactive layer is ~30 lines of inline JavaScript: clicking a
    frame rescales every frame width by `total / clicked.value`, hides
    frames that are not ancestors or descendants of the clicked one,
    and shifts the rendered tree to start at the clicked subtree's
    x-origin. Esc or clicking the title resets the view.

    No external assets — file works offline, copied to laptop, etc.
    """
    root: Dict[str, list] = {}  # {sym: [count, children]}
    total = 0
    for stack, count in folded.items():
        total += count
        node = root
        for sym in stack:
            entry = node.get(sym)
            if entry is None:
                entry = [0, {}]
                node[sym] = entry
            entry[0] += count
            node = entry[1]
    if total == 0:
        with open(path, "w") as f:
            f.write(
                '<svg xmlns="http://www.w3.org/2000/svg" width="600" '
                'height="60"><text x="20" y="40">no samples</text></svg>\n'
            )
        return

    # Walk the tree depth-first, recording rectangles per frame.
    rects: List[dict] = []
    max_depth = 0

    def walk(children: Dict[str, list], depth: int,
             x: float, w: float) -> None:
        nonlocal max_depth
        if w < SVG_MIN_FRAME_PIXELS or not children:
            return
        if depth > max_depth:
            max_depth = depth
        cur = x
        for sym, (cnt, kids) in sorted(
                children.items(), key=lambda kv: -kv[1][0]):
            cw = w * cnt / sum(v[0] for v in children.values())
            rects.append({
                "depth": depth, "x": cur, "w": cw,
                "sym": sym, "count": cnt,
            })
            walk(kids, depth + 1, cur, cw)
            cur += cw

    walk(root, 0, 0.0, float(SVG_WIDTH))

    height = SVG_TOP_PAD + (max_depth + 1) * SVG_FRAME_HEIGHT + 8

    with open(path, "w") as f:
        f.write(_render_svg(rects, total, height, title))


def _render_svg(rects: List[dict], total: int, height: int,
                title: str) -> str:
    """Build the SVG document from a precomputed rect list."""
    parts: List[str] = []
    parts.append(
        f'<?xml version="1.0" standalone="no"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{SVG_WIDTH}" height="{height}" '
        f'viewBox="0 0 {SVG_WIDTH} {height}" '
        f'font-family="Verdana, sans-serif" font-size="{SVG_FONT_SIZE}">\n'
    )
    parts.append(
        f'<rect x="0" y="0" width="{SVG_WIDTH}" height="{height}" '
        f'fill="#f0f0f0"/>\n'
    )
    parts.append(
        f'<text id="title" x="{SVG_WIDTH // 2}" y="16" '
        f'text-anchor="middle" font-weight="bold" '
        f'style="cursor: pointer">{html.escape(title)}</text>\n'
    )
    parts.append('<g id="frames">\n')
    for r in rects:
        y = SVG_TOP_PAD + r["depth"] * SVG_FRAME_HEIGHT
        colour = _frame_colour(r["sym"])
        sym_short = _short_label(r["sym"], max_len=80)
        pct = r["count"] / total * 100
        title_attr = (
            f'{html.escape(r["sym"])} '
            f'({r["count"]} samples, {pct:.2f}%)'
        )
        parts.append(
            f'<g class="frame" data-x="{r["x"]:.4f}" '
            f'data-w="{r["w"]:.4f}" data-depth="{r["depth"]}" '
            f'data-count="{r["count"]}">\n'
            f'<title>{title_attr}</title>\n'
            f'<rect x="{r["x"]:.4f}" y="{y}" '
            f'width="{r["w"]:.4f}" height="{SVG_FRAME_HEIGHT - 1}" '
            f'fill="{colour}" stroke="#888" stroke-width="0.3"/>\n'
        )
        # Only render text when the rect is wide enough to fit ≥ 4 chars.
        if r["w"] > 24:
            label = _trim_to_width(sym_short, r["w"] - 4)
            parts.append(
                f'<text x="{r["x"] + 2:.4f}" '
                f'y="{y + SVG_FRAME_HEIGHT - 4}" '
                f'pointer-events="none">{html.escape(label)}</text>\n'
            )
        parts.append('</g>\n')
    parts.append('</g>\n')
    parts.append(_INTERACTIVE_JS)
    parts.append('</svg>\n')
    return "".join(parts)


def _frame_colour(sym: str) -> str:
    """SmartMet frames in warm yellow-orange (the Brendan Gregg
    "hot" palette); non-SmartMet leaves in cool blue-grey so the
    syscall / libc rests at the top stand out visually."""
    if is_smartmet_frame(sym):
        # Stable hash → hue inside the warm band so adjacent frames
        # don't blur together.
        h = (hash(sym) & 0xFFFF) / 0xFFFF
        r = 230 + int(20 * h)
        g = 130 + int(60 * h)
        b = 50 + int(40 * h)
        return f"#{r:02x}{g:02x}{b:02x}"
    h = (hash(sym) & 0xFFFF) / 0xFFFF
    r = 120 + int(40 * h)
    g = 140 + int(40 * h)
    b = 180 + int(40 * h)
    return f"#{r:02x}{g:02x}{b:02x}"


def _trim_to_width(label: str, pixels: float) -> str:
    """Crude fixed-pitch approximation: ~6 px per char at 11 pt Verdana."""
    max_chars = max(1, int(pixels / 6))
    if len(label) <= max_chars:
        return label
    return label[: max_chars - 1] + "…"


_INTERACTIVE_JS = """\
<script><![CDATA[
(function() {
  // Click-to-zoom: rescale every frame so the clicked one fills the
  // full SVG width, hide frames that are not ancestors/descendants of
  // the clicked one. Esc / title-click resets.
  var FULL_W = %d;
  var frames = Array.prototype.slice.call(
      document.querySelectorAll('.frame'));
  function reset() {
    frames.forEach(function(g) {
      var x = parseFloat(g.dataset.x), w = parseFloat(g.dataset.w);
      var rect = g.querySelector('rect');
      var text = g.querySelector('text');
      rect.setAttribute('x', x.toFixed(4));
      rect.setAttribute('width', w.toFixed(4));
      g.style.display = '';
      if (text) {
        text.setAttribute('x', (x + 2).toFixed(4));
        text.style.display = (w > 24 ? '' : 'none');
      }
    });
  }
  function zoomTo(target) {
    var tx = parseFloat(target.dataset.x);
    var tw = parseFloat(target.dataset.w);
    var td = parseInt(target.dataset.depth, 10);
    var scale = FULL_W / tw;
    frames.forEach(function(g) {
      var x = parseFloat(g.dataset.x);
      var w = parseFloat(g.dataset.w);
      var d = parseInt(g.dataset.depth, 10);
      // Visible iff this frame's x-range overlaps the target's AND
      // it is at the target's depth or deeper (descendants), or it
      // is on the spine above the target (ancestors fully cover tx..tx+tw).
      var isDescendant = (d >= td && x >= tx - 1e-9 && x + w <= tx + tw + 1e-9);
      var isAncestor   = (d <  td && x <= tx + 1e-9 && x + w >= tx + tw - 1e-9);
      if (!isDescendant && !isAncestor) { g.style.display = 'none'; return; }
      g.style.display = '';
      var nx = (x - tx) * scale;
      if (isAncestor) { nx = 0; }
      var nw = (isAncestor ? FULL_W : w * scale);
      var rect = g.querySelector('rect');
      var text = g.querySelector('text');
      rect.setAttribute('x', nx.toFixed(4));
      rect.setAttribute('width', nw.toFixed(4));
      if (text) {
        text.setAttribute('x', (nx + 2).toFixed(4));
        text.style.display = (nw > 24 ? '' : 'none');
      }
    });
  }
  frames.forEach(function(g) {
    g.style.cursor = 'pointer';
    g.addEventListener('click', function() { zoomTo(g); });
  });
  document.getElementById('title').addEventListener('click', reset);
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') reset();
  });
})();
]]></script>
""" % SVG_WIDTH


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bperf",
        description=(
            "Capture an N-second perf profile of smartmetd, filter to "
            "SmartMet components, and emit folded.txt + graph.dot + "
            "flame.svg in OUT_DIR."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  bperf                               # 30s capture of the running smartmetd
  bperf -s 60                         # 60s capture, longer for slow paths
  bperf -p 12345 -s 10                # specific PID, short cycle
  bperf --threads request -s 30       # only stacks that handled an HTTP request
  bperf --threads background          # only background-thread stacks
  bperf --no-smartmet-only            # raw stacks (kernel + libc + everything)

The .dot file renders into SVG via:
    dot -Tsvg graph.dot > graph.svg
flame.svg is already self-contained — open it in any browser and click
any frame to zoom.
""",
    )
    p.add_argument("-p", "--pid",
                   help="PID to profile, or a process name (default: "
                        "auto-detect smartmetd).")
    p.add_argument("-s", "--seconds", type=int, default=DEFAULT_SECONDS,
                   help=f"Recording duration (default: {DEFAULT_SECONDS}s).")
    p.add_argument("-F", "--freq", type=int, default=PERF_FREQ_DEFAULT,
                   help=f"Sample frequency in Hz (default: {PERF_FREQ_DEFAULT}).")
    p.add_argument("-o", "--out-dir", default=DEFAULT_OUT_DIR,
                   help="Output directory (default: current dir).")
    p.add_argument("--threads", choices=THREAD_CLASSES,
                   default=THREAD_CLASS_ALL,
                   help="Filter stacks by thread role: 'request' keeps "
                        "only stacks that contain SmartMetPlugin::"
                        "callRequestHandler (i.e. were actively handling "
                        "an HTTP request when sampled); 'background' "
                        "keeps the rest. 'all' (default) keeps both. "
                        "Note: smartmetd does not pthread_setname_np, "
                        "so this is stack-content based, not comm based.")
    p.add_argument("--smartmet-only", dest="smartmet_only",
                   action="store_true", default=True,
                   help="(Default) Collapse stacks to SmartMet frames + "
                        "≤ 1 syscall/libc leaf.")
    p.add_argument("--no-smartmet-only", dest="smartmet_only",
                   action="store_false",
                   help="Keep raw stacks (kernel + libc + everything).")
    p.add_argument("--keep-perf-data", action="store_true",
                   help="Don't delete the intermediate perf.data file. "
                        "Useful for re-running perf script with different "
                        "options.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_argparser().parse_args(argv)
    pid = _resolve_pid(args.pid)
    _preflight(pid)

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    workdir = tempfile.mkdtemp(prefix="bperf-")
    perf_data = os.path.join(workdir, "perf.data")
    try:
        t0 = time.time()
        _run_perf_record(pid, args.seconds, perf_data, args.freq)
        print(f"bperf: parsing perf script output …", file=sys.stderr)
        text = _run_perf_script(perf_data)
        raw_stacks = parse_perf_script(text)
        if not raw_stacks:
            sys.exit("bperf: no stacks parsed (perf may have produced "
                     "an unreadable .data file)")
        unknown = estimate_unknown_ratio(raw_stacks)
        if unknown > 0.5:
            print(
                f"bperf: WARNING — {unknown:.0%} of frames are [unknown]. "
                f"Install the smartmet-server-debuginfo package so symbol "
                f"resolution works (`debuginfo-install smartmet-server` "
                f"on RHEL/Fedora).",
                file=sys.stderr,
            )
        filtered = filter_stacks(
            raw_stacks,
            thread_class=args.threads,
            smartmet_only=args.smartmet_only,
        )
        if not filtered:
            sys.exit(
                f"bperf: no stacks remain after filtering "
                f"(threads={args.threads}, smartmet_only={args.smartmet_only}). "
                f"Try --threads all and/or --no-smartmet-only."
            )
        folded = fold_stacks(filtered)
        request_count = sum(1 for s in filtered if is_request_stack(s))

        folded_path = os.path.join(out_dir, "folded.txt")
        dot_path = os.path.join(out_dir, "graph.dot")
        svg_path = os.path.join(out_dir, "flame.svg")
        label = (
            f"smartmetd pid={pid} "
            f"{args.seconds}s @ {args.freq}Hz "
            f"threads={args.threads} "
            f"smartmet_only={args.smartmet_only} "
            f"({len(filtered)} samples; {request_count} request, "
            f"{len(filtered) - request_count} background)"
        )
        write_folded(folded, folded_path)
        write_dot(folded, dot_path, label=label)
        write_svg(folded, svg_path, title=label)

        elapsed = time.time() - t0
        print(
            f"bperf: done in {elapsed:.1f}s — wrote:\n"
            f"  {folded_path}\n"
            f"  {dot_path}   (render: dot -Tsvg {dot_path} > graph.svg)\n"
            f"  {svg_path}   (open in any browser, click to zoom)",
            file=sys.stderr,
        )
    finally:
        if not args.keep_perf_data:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print(f"bperf: kept perf.data at {perf_data}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
