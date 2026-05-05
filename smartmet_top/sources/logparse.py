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

# 1-deep last-seen cache. On a busy backend, the access-log cleaner
# flushes lines in 5-second bursts, so consecutive records often
# share the same second. Caching just the previous (key, epoch) pair
# turns most parse_iso calls into a string equality test plus a
# tuple read — about 6 ns. The fractional second is dropped (no
# consumer uses sub-second precision; minute-bucketing and the
# IP Flow playhead both round to seconds) so two records in the
# same wall-clock second share a cache key.
_LAST_TS: tuple = ("", 0.0)


def parse_iso(s: str) -> float:
    """Hand-parse the AccessLogger's fixed-format timestamp to epoch.

    Format: ``YYYY-MM-DDTHH:MM:SS[,.]ffffff`` (10 + T + 8 + sep + frac).
    Treats the time as local — SmartMet writes server-local
    timestamps and we want to preserve that for sparkline / scrub
    alignment.

    Two caches:

      * ``_LAST_TS`` — single-entry cache of the most recent
        ``(YYYY-MM-DDTHH:MM:SS, epoch_seconds)`` pair. Hits on every
        record that shares the same second as the previous one,
        which is most records during a 5-second log-flush burst.

      * ``_MIDNIGHT_CACHE`` — per-day midnight-epoch cache so the
        slow ``time.mktime`` (consults local tz files) runs once
        per unique date in the replay set rather than per line.

    Falls back to ``time.time()`` for malformed input so a single
    bad line doesn't poison a multi-million-line replay.
    """
    global _LAST_TS
    # Truncate to second precision.  s[:19] is "YYYY-MM-DDTHH:MM:SS".
    last = _LAST_TS
    key = s[:19]
    if last[0] == key:
        return last[1]
    try:
        date_key = s[:10]
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
        result = midnight + h * 3600 + mn * 60 + sec
        _LAST_TS = (key, result)
        return result
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
