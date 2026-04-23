"""CSV / JSON export of panel snapshots."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import time
from typing import List, Sequence, Tuple


def write_csv(path: str, headers: Sequence[str], rows: Sequence[Sequence]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(headers))
        for r in rows:
            w.writerow(list(r))


def write_json(path: str, headers: Sequence[str], rows: Sequence[Sequence]) -> None:
    records = [dict(zip(headers, r)) for r in rows]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, default=str, indent=2)


def export_path(panel_name: str, fmt: str, dir: str = None) -> str:
    if dir is None:
        dir = os.environ.get("SMARTMET_TOP_EXPORT_DIR") or tempfile.gettempdir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = panel_name.lower().replace(" ", "-")
    return os.path.join(dir, f"smartmet-top-{safe}-{stamp}.{fmt}")


def save_snapshot(panel_name: str,
                  headers: Sequence[str],
                  rows: Sequence[Sequence],
                  fmt: str = "csv",
                  dir: str = None) -> str:
    """Write and return the path."""
    path = export_path(panel_name, fmt, dir)
    if fmt == "csv":
        write_csv(path, headers, rows)
    elif fmt == "json":
        write_json(path, headers, rows)
    else:
        raise ValueError(f"unknown export format: {fmt}")
    return path
