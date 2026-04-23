"""Command-line entry point: `python -m smartmet_top`."""

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import List

from .app import run_app

DEFAULT_LOG_GLOB = "/var/log/smartmet/*-access-log"


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="smartmet-top",
        description="Interactive terminal monitor for SmartMet Server.",
    )
    p.add_argument(
        "-l", "--log", action="append", default=[],
        metavar="PATH-OR-GLOB",
        help="Access-log file (may be given multiple times; globs allowed). "
             f"Default if omitted: {DEFAULT_LOG_GLOB}",
    )
    p.add_argument(
        "-u", "--admin-url", default=None, metavar="URL",
        help="Admin plugin base URL, e.g. http://localhost:8080/admin. "
             "If omitted, admin-only panels will be empty.",
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
        "--replay", action="store_true",
        help="On startup, read the tail of each log file (up to 256 MB) "
             "so the panels come up populated instead of empty.",
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

    if not log_paths and not args.admin_url:
        sys.stderr.write(
            "smartmet-top: no data sources. Pass -l LOG_FILE or -u ADMIN_URL.\n"
        )
        return 2

    try:
        run_app(
            log_paths=log_paths,
            admin_url=args.admin_url,
            admin_interval=args.admin_interval,
            replay=args.replay,
        )
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
