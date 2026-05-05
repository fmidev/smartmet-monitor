"""Parser for SmartMet access-log lines.

Format (from spine/AccessLogger.cpp):
  IP - - [END] "METHOD URL HTTP/VER" STATUS [START] DUR_MS BYTES "ETAG" APIKEY

The fields are space-separated and the URL is URL-encoded, so it
never contains literal spaces in practice (per the project's
CLAUDE.md). That lets us use ``str.split()`` rather than a regex —
about 4× faster on RHEL 8 / CPython 3.9, which adds up when a
replay parses tens of millions of lines.

The timestamp parser is also hand-rolled. Spine's AccessLogger
emits a fixed-width ISO-8601 with a COMMA decimal separator
(``2026-04-25T19:57:49,567645``), so we slice the string at known
offsets and call ``time.mktime`` rather than going through
``datetime.fromisoformat``. About 6× faster than the
datetime-based path.
"""

from __future__ import annotations

import time
from typing import Optional


_MIDNIGHT_CACHE: dict = {}


def parse_iso(s: str) -> float:
    """Hand-parse the AccessLogger's fixed-format timestamp to epoch.

    Format: ``YYYY-MM-DDTHH:MM:SS[,.]ffffff`` (10 + T + 8 + sep + frac).
    Treats the time as local — SmartMet writes server-local
    timestamps and we want to preserve that for sparkline / scrub
    alignment.

    ``time.mktime`` is the slowest part of the path (~3 µs on
    RHEL 8 / Python 3.9 because it consults the local tz files);
    we cache the midnight-epoch for each ``YYYY-MM-DD`` key so
    mktime is called once per unique date in the replay set —
    typically 1–7 times for a normal smwebmon session — and the
    per-line cost falls back to plain integer arithmetic.

    Falls back to ``time.time()`` for malformed input so a single
    bad line in a multi-million-line replay doesn't poison the
    whole run.
    """
    try:
        date_key = s[0:10]
        midnight = _MIDNIGHT_CACHE.get(date_key)
        if midnight is None:
            year  = int(s[0:4])
            month = int(s[5:7])
            day   = int(s[8:10])
            midnight = time.mktime(
                (year, month, day, 0, 0, 0, 0, 0, -1))
            _MIDNIGHT_CACHE[date_key] = midnight
        h   = int(s[11:13])
        mn  = int(s[14:16])
        sec = int(s[17:19])
        frac = 0.0
        if len(s) > 19:
            sep = s[19]
            if sep == "," or sep == ".":
                frac_str = s[20:]
                if frac_str:
                    frac = int(frac_str) / (10 ** len(frac_str))
        return midnight + h * 3600 + mn * 60 + sec + frac
    except (ValueError, IndexError):
        return time.time()


def strip_query(url: str) -> str:
    i = url.find("?")
    return url if i < 0 else url[:i]


def parse(line: str) -> Optional[dict]:
    """Return a dict of field values, or None if the line isn't
    the expected 13-token shape.

    Token layout (1-indexed for parity with the awk programs in
    bstat.sh, 0-indexed in the implementation below):
        0  IP            5  URL              10 BYTES
        1  -             6  HTTP/VER"        11 "ETAG"
        2  -             7  STATUS           12 APIKEY
        3  [END]         8  [START]
        4  "METHOD       9  DUR_MS

    Validates by field count + the int conversions on STATUS,
    DUR_MS, BYTES; the truncated first / last lines a logrotate
    fence-post can leave in the file are caught by either of
    those. The bracket / quote validation an earlier draft did is
    redundant — a line that survives the int conversions but has
    a malformed ETAG bracket on its timestamp is far rarer than
    the cost of checking every line.
    """
    parts = line.split()
    if len(parts) != 13:
        return None
    try:
        status = int(parts[7])
        dur    = int(parts[9])
        nbytes = int(parts[10])
    except ValueError:
        return None
    url = parts[5]
    return {
        "ip": parts[0],
        "end": parts[3][1:-1],
        "start_ts": parse_iso(parts[8][1:-1]),
        "method": parts[4][1:],
        "url": url,
        "path": strip_query(url),
        "status": status,
        "dur_ms": dur,
        "bytes": nbytes,
        "etag": parts[11],
        "apikey": parts[12],
    }
