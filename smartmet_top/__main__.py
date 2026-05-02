"""Command-line entry point: `python -m smartmet_top`."""

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import List

from .app import run_app
from .state.store import set_history_minutes
from .widgets.bars import set_ascii

DEFAULT_LOG_GLOB = "/var/log/smartmet/*-access-log"


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="smtop",
        description="Interactive terminal monitor for SmartMet Server.",
    )
    p.add_argument(
        "-l", "--log", action="append", default=[],
        metavar="PATH-OR-GLOB",
        help="Access-log file (may be given multiple times; globs allowed). "
             f"Default if omitted: {DEFAULT_LOG_GLOB}",
    )
    p.add_argument(
        "-u", "--admin-url", action="append", default=[], metavar="URL",
        help="Admin-plugin base URL, e.g. http://localhost:8080/admin. "
             "May be given multiple times, or as a comma-separated list, "
             "to monitor multiple hosts. Each URL can be prefixed with a "
             "label via LABEL=URL (otherwise the hostname is used).",
    )
    p.add_argument(
        "-n", "--admin-interval", type=float, default=2.0, metavar="SEC",
        help="Admin-plugin poll interval in seconds (default: 2.0).",
    )
    p.add_argument(
        "--no-logs", action="store_true",
        help="Don't tail any log file (use admin /lastrequests instead).",
    )
    p.add_argument(
        "--no-admin", action="store_true",
        help="Skip the localhost admin-port auto-probe. Without -u and "
             "without this flag, smtop probes http://localhost:8080/admin "
             "(frontend) and http://localhost:8081/admin (backend) at "
             "startup and registers whichever responds.",
    )
    p.add_argument(
        "--replay", action="store_true",
        help="On startup, read the tail of each log file so the panels "
             "come up populated instead of empty. Capped at "
             "--replay-bytes per file (default 1 GB).",
    )
    p.add_argument(
        "--replay-bytes", type=int, default=1024 * 1024 * 1024,
        metavar="N",
        help="Maximum number of bytes to read per log file when "
             "--replay is in effect. Default 1 GB. Raise this on "
             "low-traffic logs where 1 GB doesn't cover the desired "
             "history window; lower it on busy logs to reduce startup "
             "time. Note: gzipped rotated logs are read in full "
             "regardless of this cap (gzip doesn't support cheap "
             "seek-to-tail).",
    )
    p.add_argument(
        "--include-rotated", action="store_true",
        help="When --replay is in effect, also read each log file's "
             "rotated siblings (e.g. wms-access-log-20260424.gz). "
             "Files are read in chronological order — historical first, "
             "live last — so the store sees a continuous timeline. "
             "Combine with --history-minutes to keep the historical "
             "data instead of pruning it back to 60 minutes.",
    )
    p.add_argument(
        "--history-minutes", type=int, default=1440, metavar="N",
        help="Per-minute history retention. Default 1440 minutes "
             "(24 hours). Raise to 10080 for a full week, lower for "
             "less memory on very tight hosts. Memory grows roughly "
             "linearly: ~12 KB per minute on a 20-plugin host, so "
             "24 h ≈ 17 MB and 7 days ≈ 120 MB.",
    )
    p.add_argument(
        "--ascii", action="store_true",
        help="Render charts with eighth-block characters instead of "
             "Braille. Use this on terminals or fonts that don't render "
             "U+2800..U+28FF correctly.",
    )
    p.add_argument(
        "--perf", action="store_true",
        help="Enable the perf sampler in the Proc panel. Spawns "
             "`perf record -F 99 -g -p PID -- sleep 1` periodically "
             "for the selected smartmetd PID and renders top-symbols "
             "and a live flamegraph. Adds ~10%% CPU overhead to the "
             "target during the recording second of each cycle. "
             "Requires perf installed and either root or "
             "kernel.perf_event_paranoid <= 2. "
             "Also auto-starts the off-CPU sampler — press 'o' in the "
             "Flame view to switch between on-CPU and off-CPU "
             "flamegraphs (off-CPU needs bcc-tools / offcputime-bpfcc; "
             "the panel surfaces an install hint when missing).",
    )
    p.add_argument(
        "--perf-interval", type=float, default=10.0, metavar="SEC",
        help="Full perf cycle in seconds (record + idle remainder). "
             "Default 10.0.",
    )
    p.add_argument(
        "--perf-record-seconds", type=int, default=3, metavar="N",
        help="How long to record per perf cycle. Default 3 seconds. "
             "Longer = more samples per flamegraph (denser, more "
             "representative of typical behaviour) but proportionally "
             "more CPU overhead on the target during the recording "
             "window. Combined with the default --perf-interval=10 "
             "the duty cycle is ~30%%.",
    )
    p.add_argument(
        "--journal-unit", type=str,
        default="smartmet-backend,smartmet-frontend",
        metavar="UNIT[,UNIT...]",
        help="systemd unit(s) to tail in the Logs panel as a "
             "[journal] source. Comma-separated for multiple units; "
             "lines from all of them are merged into one timestamp-"
             "ordered stream. Default: "
             "smartmet-backend,smartmet-frontend (covers a SmartMet "
             "host running the backend daemon, the frontend daemon, "
             "or both). Pass an empty string (--journal-unit '') to "
             "disable.",
    )
    p.add_argument(
        "--malloc-flame", nargs="?", type=int, const=4096, default=None,
        metavar="MIN_BYTES",
        help="Enable the per-allocation flamegraph (Flame view 'A' "
             "mode). Records every malloc() ≥ MIN_BYTES from "
             "smartmetd via a bpftrace uprobe and weights stacks by "
             "total bytes allocated. Default MIN_BYTES is 4096; pass "
             "an explicit number to override (e.g. --malloc-flame 1024). "
             "MIN_BYTES=0 traces every malloc — extreme overhead. "
             "WARNING: NOT FOR PRODUCTION. Uprobes on a hot allocator "
             "function can add measurable latency to every alloc; on a "
             "busy SmartMet backend this can slow request handling "
             "visibly. Use on dev / staging only. Allocator is "
             "auto-detected (jemalloc / mimalloc / glibc).",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    # expand globs and dedupe
    log_paths: List[str] = []
    if not args.no_logs:
        patterns = args.log or [DEFAULT_LOG_GLOB]
        seen = set()
        for pat in patterns:
            for p in glob.glob(pat) or ([pat] if not any(c in pat for c in "*?[") else []):
                ap = os.path.abspath(p)
                if ap not in seen and os.path.isfile(ap):
                    seen.add(ap)
                    log_paths.append(ap)

    admin_urls = _parse_admin_urls(args.admin_url)
    # Auto-probe localhost ports when the operator gave no -u flag.
    # Saves typing on the (very common) "monitor the SmartMet running
    # right here" workflow. Explicit -u still wins; --no-admin opts
    # out entirely.
    if not admin_urls and not args.no_admin:
        from .sources.adminapi import probe_default_admin_urls
        admin_urls = probe_default_admin_urls()
        if admin_urls:
            sys.stderr.write(
                "smtop: auto-discovered admin URLs: "
                + ", ".join(f"{l}={u}" for l, u in admin_urls) + "\n"
            )

    if args.ascii:
        set_ascii(True)
    set_history_minutes(args.history_minutes)

    # Without log files and admin URLs the access-log panels stay empty,
    # but the Proc panel still works against any local smartmetd process.
    # We let the dashboard start so the operator can use Proc directly.
    if not log_paths and not admin_urls:
        sys.stderr.write(
            "smtop: no log files or admin URLs configured; "
            "Proc panel will still work for local smartmetd processes.\n"
        )

    try:
        run_app(
            log_paths=log_paths,
            admin_urls=admin_urls,
            admin_interval=args.admin_interval,
            replay=args.replay,
            replay_bytes=args.replay_bytes,
            include_rotated=args.include_rotated,
            enable_perf=args.perf,
            perf_interval=args.perf_interval,
            perf_record_seconds=args.perf_record_seconds,
            malloc_flame_min_bytes=args.malloc_flame,
            journal_unit=args.journal_unit,
        )
    except KeyboardInterrupt:
        pass
    return 0


def _parse_admin_urls(raw: List[str]) -> List[tuple]:
    """Expand comma-separated lists and derive a short host label per URL.

    Returns a list of (label, url) tuples, label guaranteed unique.
    """
    from urllib.parse import urlparse

    out: List[tuple] = []
    used = set()
    for item in raw:
        for piece in item.split(","):
            piece = piece.strip()
            if not piece:
                continue
            # optional LABEL=URL form
            if "=" in piece and not piece.startswith(("http://", "https://")):
                label, _, url = piece.partition("=")
                label = label.strip()
                url = url.strip()
            else:
                url = piece
                host = urlparse(url).hostname or url
                label = host
            # dedupe labels
            base = label
            n = 2
            while label in used:
                label = f"{base}#{n}"
                n += 1
            used.add(label)
            out.append((label, url))
    return out


if __name__ == "__main__":
    sys.exit(main())
