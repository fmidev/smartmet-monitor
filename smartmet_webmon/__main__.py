"""``python -m smartmet_webmon`` — daemon entry point for ``smwebmon``."""

from __future__ import annotations

import argparse
import asyncio
import glob
import os
import signal
import sys
from typing import List, Tuple

from smartmet_top.runtime import replay_logs, start_sources
from smartmet_top.state.store import Store, set_history_minutes
from smartmet_top.__main__ import _parse_admin_urls

from . import assets
from .server import WebServer


DEFAULT_LOG_GLOB = "/var/log/smartmet/*-access-log"
DEFAULT_BIND = "127.0.0.1:8765"


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="smwebmon",
        description=(
            "Browser dashboard for SmartMet Server. Imports the same "
            "data sources as smtop and serves them over HTTP for "
            "loopback-only operator use. Tunnel via SSH and open "
            "http://localhost:<port>/ in a browser."
        ),
    )
    p.add_argument(
        "--bind", default=DEFAULT_BIND, metavar="HOST:PORT",
        help=f"Listen address (default: {DEFAULT_BIND}). Stick to "
             f"localhost unless you've put auth in front; the server "
             f"is unauthenticated.",
    )
    p.add_argument(
        "-l", "--log", action="append", default=[], metavar="PATH-OR-GLOB",
        help="Access-log file to tail (may be repeated; globs allowed). "
             f"Default if omitted: {DEFAULT_LOG_GLOB}",
    )
    p.add_argument(
        "-u", "--admin-url", action="append", default=[], metavar="URL",
        help="Admin-plugin base URL (may be repeated or comma-separated). "
             "Each may be prefixed LABEL=URL.",
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
             "without this flag, smwebmon probes "
             "http://localhost:8080/admin (frontend) and "
             "http://localhost:8081/admin (backend) at startup and "
             "registers whichever responds.",
    )
    p.add_argument(
        "--replay", action=argparse.BooleanOptionalAction, default=True,
        help="On startup, read the tail of each log file so the URLs "
             "panel comes up populated instead of empty. Default ON; "
             "pass --no-replay to opt out.",
    )
    p.add_argument(
        "--replay-bytes", type=int, default=1024 * 1024 * 1024,
        metavar="N",
        help="Max bytes to read per log file when --replay is set.",
    )
    p.add_argument(
        "--include-rotated", action="store_true",
        help="With --replay, also read rotated log siblings.",
    )
    p.add_argument(
        "--perf", action=argparse.BooleanOptionalAction, default=True,
        help="Enable the perf samplers behind the Flame panel: on-CPU "
             "(perf record), off-CPU (offcputime-bpfcc), page-fault, "
             "wakeup, block-I/O, plus the biolat / runqlat / perfstat "
             "host-wide samplers. Same scope as smtop --perf. Default "
             "ON; pass --no-perf to opt out. Requires perf installed "
             "and kernel.perf_event_paranoid <= 2 (the RHEL default). "
             "bcc-tools is recommended for off-CPU; the panel falls "
             "back gracefully when it's missing.",
    )
    p.add_argument(
        "--perf-interval", type=float, default=10.0, metavar="SEC",
        help="Full perf cycle in seconds (record + idle remainder). "
             "Default 10.0.",
    )
    p.add_argument(
        "--perf-record-seconds", type=int, default=3, metavar="N",
        help="Seconds to record per perf cycle. Default 3. Combined "
             "with --perf-interval=10 the duty cycle on the target "
             "smartmetd process is ~30%%.",
    )
    p.add_argument(
        "--malloc-flame", nargs="?", type=int, const=4096, default=None,
        metavar="MIN_BYTES",
        help="Enable per-allocation flame graph (uprobe-on-malloc). "
             "Default OFF — uprobe-on-malloc on a busy server has "
             "measurable overhead; opt in explicitly. MIN_BYTES caps "
             "which allocs are traced (default 4096 when the flag is "
             "given without a value).",
    )
    p.add_argument(
        "--history-minutes", type=int, default=1440, metavar="N",
        help="Per-minute history retention (default: 1440 = 24h).",
    )
    p.add_argument(
        "--journal-unit", type=str, default="smartmet-server",
        metavar="UNIT",
        help="systemd unit to follow as a [journal] source. Pass an "
             "empty string to disable.",
    )
    p.add_argument(
        "--asset-root", default=None, metavar="PATH",
        help="Override the static-asset directory. Default: search "
             "$SMARTMET_WEBMON_ASSETS, then sibling share/smartmet/"
             "webmon/, then /usr/share/smartmet/webmon/.",
    )
    return p.parse_args(argv)


def _split_bind(spec: str) -> Tuple[str, int]:
    if ":" not in spec:
        raise SystemExit(f"smwebmon: --bind must be HOST:PORT, got {spec!r}")
    host, _, port = spec.rpartition(":")
    try:
        return host or "127.0.0.1", int(port)
    except ValueError:
        raise SystemExit(f"smwebmon: invalid port in --bind {spec!r}")


def _expand_logs(args) -> List[str]:
    if args.no_logs:
        return []
    patterns = args.log or [DEFAULT_LOG_GLOB]
    seen, out = set(), []
    for pat in patterns:
        matches = glob.glob(pat)
        if not matches and not any(c in pat for c in "*?["):
            matches = [pat]
        for p in matches:
            ap = os.path.abspath(p)
            if ap not in seen and os.path.isfile(ap):
                seen.add(ap)
                out.append(ap)
    return out


async def _run(args: argparse.Namespace) -> int:
    asset_root = args.asset_root or assets.resolve_asset_root()
    if asset_root is None:
        sys.stderr.write(
            "smwebmon: cannot find webmon assets. Looked in: "
            + ", ".join(assets.candidate_paths())
            + "\nSet SMARTMET_WEBMON_ASSETS or pass --asset-root.\n"
        )
        return 2

    set_history_minutes(args.history_minutes)
    store = Store()
    log_paths = _expand_logs(args)
    admin_urls = _parse_admin_urls(args.admin_url)
    if not admin_urls and not args.no_admin:
        from smartmet_top.sources.adminapi import probe_default_admin_urls
        admin_urls = probe_default_admin_urls()
        if admin_urls:
            sys.stderr.write(
                "smwebmon: auto-discovered admin URLs: "
                + ", ".join(f"{l}={u}" for l, u in admin_urls) + "\n"
            )

    bind = _split_bind(args.bind)
    server = WebServer(store, bind=bind, asset_root=asset_root)
    server.start()
    sys.stderr.write(
        f"smwebmon: listening on http://{bind[0]}:{server.port}/  "
        f"(assets: {asset_root})\n"
    )

    if args.replay:
        await replay_logs(
            store, log_paths,
            replay_bytes=args.replay_bytes,
            include_rotated=args.include_rotated,
        )

    tasks = start_sources(
        store,
        log_paths=log_paths,
        admin_urls=admin_urls,
        admin_interval=args.admin_interval,
        enable_perf=args.perf,
        perf_interval=args.perf_interval,
        perf_record_seconds=args.perf_record_seconds,
        malloc_flame_min_bytes=args.malloc_flame,
        journal_unit=args.journal_unit,
    )

    stop = asyncio.Event()

    def _on_signal(*_):
        stop.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows / restricted environments — fall back to KeyboardInterrupt.
            pass

    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        # Let cancellations propagate.
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        server.stop()
    return 0


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
