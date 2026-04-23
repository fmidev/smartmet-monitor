"""Parser for SmartMet access-log lines.

Format (from spine/AccessLogger.cpp):
  IP - - [END] "METHOD URL HTTP/VER" STATUS [START] DUR_MS BYTES "ETAG" APIKEY
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

# Explanation of the regex:
#   (\S+)                      IP
#   \s+\S+\s+\S+\s+            "- -"
#   \[(\S+)\]\s+               [END]
#   "(\S+)\s+(.*?)\s+HTTP/\S+" "METHOD URL HTTP/VER"  (URL may rarely contain spaces)
#   \s+(\d+)\s+                STATUS
#   \[(\S+)\]\s+               [START]
#   (\d+)\s+(\d+)\s+           DUR_MS BYTES
#   (\S+)\s+(\S+)              ETAG APIKEY
_LINE_RE = re.compile(
    r"^(\S+)\s+\S+\s+\S+\s+"
    r"\[([^\]]+)\]\s+"
    r'"(\S+)\s+(.*?)\s+HTTP/\S+"\s+'
    r"(\d+)\s+"
    r"\[([^\]]+)\]\s+"
    r"(\d+)\s+(\d+)\s+"
    r"(\S+)\s+(\S+)\s*$"
)


def parse_iso(s: str) -> float:
    """Parse an ISO-8601 local timestamp like '2026-04-23T07:00:00.125' to epoch."""
    # fromisoformat in 3.9 accepts 'YYYY-MM-DDTHH:MM:SS[.ffffff]'
    # but SmartMet logs use fractional seconds with 3 digits; that's fine.
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return time.time()
    # Treat as local time (SmartMet logs are server-local).
    return dt.timestamp()


def strip_query(url: str) -> str:
    i = url.find("?")
    return url if i < 0 else url[:i]


def parse(line: str) -> Optional[dict]:
    m = _LINE_RE.match(line)
    if m is None:
        return None
    ip, end, method, url, status, start, dur, nbytes, etag, apikey = m.groups()
    return {
        "ip": ip,
        "end": end,
        "start_ts": parse_iso(start),
        "method": method,
        "url": url,
        "path": strip_query(url),
        "status": int(status),
        "dur_ms": int(dur),
        "bytes": int(nbytes),
        "etag": etag,
        "apikey": apikey,
    }
