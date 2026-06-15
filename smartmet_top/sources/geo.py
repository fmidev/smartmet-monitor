"""IP → ISO country code lookup, sourced from RIR delegated-stats files.

Each Regional Internet Registry (APNIC, RIPE, ARIN, LACNIC, AFRINIC)
publishes a daily plaintext file listing every IPv4 / IPv6 allocation
with the receiving country. We parse the union of those files into
two sorted lists — one for IPv4 (32-bit start/end ints), one for IPv6
(128-bit start/end ints) — and look up via bisect.

Pure stdlib by design (matches the rest of this package). No pip
package, no signup, no licence-attribution to thread through the UI.
The trade-off versus MaxMind GeoLite2 is granularity: we get a
country per netblock, not a city — fine for "where is this traffic
coming from?" and a privacy win at the same time.

File format (RFC-style, pipe-delimited):

    registry|cc|type|start|value|date|status[|hash]

For ``type=ipv4``: ``start`` is dotted-quad, ``value`` is the count of
addresses in the block (start..start+value-1).
For ``type=ipv6``: ``start`` is an IPv6 prefix and ``value`` is the
prefix length, so the block covers 2^(128-prefix) addresses.

Lines we skip:
    * version header   (starts with "2|")
    * summary lines    (cc=='*' or status=='summary')
    * non-allocations  (status not in {allocated, assigned})
"""

from __future__ import annotations

import bisect
import os
from typing import Iterable, List, Optional, Tuple


# Status field values that represent a real assignment to a country.
# `available`, `reserved`, and the various "ietf"/"iana" buckets all
# carry country=ZZ or empty; we drop them.
_KEEP_STATUS = frozenset(("allocated", "assigned"))


def _ipv4_to_int(s: str) -> Optional[int]:
    parts = s.split(".")
    if len(parts) != 4:
        return None
    try:
        a, b, c, d = (int(p) for p in parts)
    except ValueError:
        return None
    if not all(0 <= x <= 255 for x in (a, b, c, d)):
        return None
    return (a << 24) | (b << 16) | (c << 8) | d


def _ipv6_to_int(s: str) -> Optional[int]:
    """Parse an IPv6 address (possibly with `::` compression) to a
    128-bit int. RIR files don't use mixed v4-in-v6 forms, so we only
    handle pure-v6 syntax."""
    if "::" in s:
        left, _, right = s.partition("::")
        lparts = left.split(":") if left else []
        rparts = right.split(":") if right else []
        missing = 8 - len(lparts) - len(rparts)
        if missing < 0:
            return None
        groups = lparts + ["0"] * missing + rparts
    else:
        groups = s.split(":")
        if len(groups) != 8:
            return None
    try:
        n = 0
        for g in groups:
            n = (n << 16) | int(g or "0", 16)
        return n
    except ValueError:
        return None


def parse_delegated(text: str) -> Iterable[Tuple[str, int, int, int]]:
    """Yield ``(family, start_int, end_int, cc)`` for every real
    allocation line in `text`. ``family`` is 4 or 6.

    The function is a pure parser — feed it the contents of any single
    delegated-stats file and it yields one record per allocation. The
    caller is expected to pool records from multiple RIR files into a
    single sorted index.
    """
    for line in text.splitlines():
        if not line or line[0] in "#2":
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        # registry, cc, type, start, value, date, status [, hash]
        cc = parts[1]
        typ = parts[2]
        start = parts[3]
        value = parts[4]
        status = parts[6]
        if cc == "*" or status not in _KEEP_STATUS:
            continue
        if typ == "ipv4":
            s = _ipv4_to_int(start)
            try:
                count = int(value)
            except ValueError:
                continue
            if s is None or count <= 0:
                continue
            yield 4, s, s + count - 1, cc
        elif typ == "ipv6":
            s = _ipv6_to_int(start)
            try:
                prefix = int(value)
            except ValueError:
                continue
            if s is None or not (0 <= prefix <= 128):
                continue
            span = 1 << (128 - prefix)
            yield 6, s, s + span - 1, cc


class CountryDB:
    """In-memory, bisect-backed netblock → country index.

    Two parallel sorted arrays per family: ``starts`` and ``ends`` are
    ints, ``ccs`` is the country code at the same index. Lookup is one
    ``bisect_right`` call followed by a range check — each query takes
    a couple of microseconds on a modern CPU.
    """

    __slots__ = ("v4_starts", "v4_ends", "v4_ccs",
                 "v6_starts", "v6_ends", "v6_ccs",
                 "loaded_paths", "stat")

    def __init__(self) -> None:
        self.v4_starts: List[int] = []
        self.v4_ends:   List[int] = []
        self.v4_ccs:    List[str] = []
        self.v6_starts: List[int] = []
        self.v6_ends:   List[int] = []
        self.v6_ccs:    List[str] = []
        self.loaded_paths: List[str] = []
        # {cc: total_address_count} — debug-only; populated by ``load``.
        self.stat: dict = {}

    def load(self, paths: Iterable[str]) -> None:
        """Read & merge every file in `paths` (RIR delegated-stats
        format). Re-sorts on each call. Allocations with overlapping
        ranges are kept in the order they're read; the last-loaded
        wins for any disputed IP — but in practice the five RIR files
        partition the address space cleanly, so overlaps are rare."""
        v4: List[Tuple[int, int, str]] = []
        v6: List[Tuple[int, int, str]] = []
        loaded = []
        for p in paths:
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            loaded.append(p)
            for fam, s, e, cc in parse_delegated(text):
                (v4 if fam == 4 else v6).append((s, e, cc))
        v4.sort()
        v6.sort()
        self.v4_starts = [r[0] for r in v4]
        self.v4_ends   = [r[1] for r in v4]
        self.v4_ccs    = [r[2] for r in v4]
        self.v6_starts = [r[0] for r in v6]
        self.v6_ends   = [r[1] for r in v6]
        self.v6_ccs    = [r[2] for r in v6]
        self.loaded_paths = loaded

        stat: dict = {}
        for s, e, cc in v4:
            stat[cc] = stat.get(cc, 0) + (e - s + 1)
        self.stat = stat

    def __bool__(self) -> bool:
        return bool(self.v4_starts) or bool(self.v6_starts)

    def lookup_int(self, ip_int: int, family: int = 4) -> str:
        """Return the 2-letter country code for `ip_int`, or ``"??"``
        when no allocation covers it (private space, reserved blocks,
        IETF assignments, etc.)."""
        if family == 4:
            starts = self.v4_starts
            ends = self.v4_ends
            ccs = self.v4_ccs
        else:
            starts = self.v6_starts
            ends = self.v6_ends
            ccs = self.v6_ccs
        if not starts:
            return "??"
        i = bisect.bisect_right(starts, ip_int) - 1
        if i < 0:
            return "??"
        if ip_int <= ends[i]:
            return ccs[i]
        return "??"

    def lookup(self, ip: str) -> str:
        if not ip or ip == "-":
            return "??"
        if ":" in ip:
            n = _ipv6_to_int(ip)
            return self.lookup_int(n, 6) if n is not None else "??"
        n = _ipv4_to_int(ip)
        return self.lookup_int(n, 4) if n is not None else "??"


# Default search paths — operators can override via the `--country-db`
# flag (which accepts a directory or a single file). The first path
# that exists wins; we don't merge across them.
DEFAULT_DB_DIRS = (
    # Operator-managed override location (e.g. a daily refresh
    # cron). Wins if populated.
    "/var/lib/smartmet-monitor",
    # RPM-shipped snapshot — the smartmet-webmon package installs
    # the five delegated-stats files here so the Countries panel
    # works out-of-the-box on a fresh install. Test-phase only;
    # later replaced by an explicit refresh mechanism.
    "/usr/share/smartmet/country-db",
    # Dev-box convenience path for ad-hoc local testing.
    "/tmp/smartmet-rir",
)


def discover_db_files(custom: Optional[str] = None) -> List[str]:
    """Pick the country-db files to load. ``custom`` may be a single
    file or a directory of `delegated-*-extended-latest` files. When
    not provided, we search ``DEFAULT_DB_DIRS`` in order and return
    whichever directory has at least one matching file."""
    candidates: List[str] = []
    if custom:
        if os.path.isfile(custom):
            return [custom]
        if os.path.isdir(custom):
            candidates = [
                os.path.join(custom, fn) for fn in sorted(os.listdir(custom))
                if fn.startswith("delegated-") and fn.endswith("-latest")
            ]
            return candidates
        return []
    for d in DEFAULT_DB_DIRS:
        if not os.path.isdir(d):
            continue
        files = [
            os.path.join(d, fn) for fn in sorted(os.listdir(d))
            if fn.startswith("delegated-") and fn.endswith("-latest")
        ]
        if files:
            return files
    return []
