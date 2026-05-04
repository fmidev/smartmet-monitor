"""Allocator-stats poller for the SmartMet ``?what=mallocstats`` endpoint.

Spine 26.4.27+ exposes the running process's allocator stats via the
admin plugin: a single GET returns either jemalloc's JSON dump
(default ``opts=J``) or mimalloc's text dump. We parse the top-level
fields each cycle and drop the bulky per-arena / per-bin structure —
those are useful for one-off forensic dives but would dominate the
store's memory footprint if we kept the full dump in a 60-minute ring.

The fields we keep (jemalloc):

  * ``allocated``  — bytes user-requested, currently not freed
  * ``active``     — bytes in active extents (``allocated`` plus
                      free space jemalloc holds for reuse)
  * ``metadata``   — overhead bytes (book-keeping)
  * ``resident``   — bytes physically resident (RSS attributable
                      to the allocator)
  * ``mapped``     — bytes mapped from kernel (allocator's virtual
                      footprint)
  * ``retained``   — bytes munmaped but kept reserved for fast
                      re-acquire; counts towards address-space use
                      but not RSS
  * ``narenas``    — arena count
  * ``version``    — jemalloc version string

Plus one derived metric the panel surfaces as a colour:

  * ``fragmentation_pct`` = ``(active - allocated) / active``,
    expressed as a percentage. Healthy SmartMet backends sit
    around 5-15%; sustained >30% suggests the allocator is
    holding a lot of free space the application keeps not
    reclaiming.

Cadence: 30 seconds. The numbers don't change at sub-second rates,
the JSON dump is large (~50 KB on a backend with 32 arenas), and
spine's epoch refresh on the call costs ~10-100 µs.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


MALLOCSTATS_INTERVAL = 30.0  # seconds; see module docstring
MALLOCSTATS_TIMEOUT = 5.0    # spine's epoch refresh is fast, but a busy
                             # host can occasionally take a moment;
                             # 5 s is generous without freezing the
                             # poll loop.
MALLOCSTATS_PATH = "?what=mallocstats&opts=J"


@dataclass
class MallocSample:
    """One snapshot of allocator state. Times are unix seconds."""

    ts: float = 0.0
    allocator: str = ""              # "jemalloc" / "mimalloc" / "glibc" / "unknown"
    version: str = ""
    allocated: int = 0
    active: int = 0
    metadata: int = 0
    resident: int = 0
    mapped: int = 0
    retained: int = 0
    narenas: int = 0

    @property
    def fragmentation_pct(self) -> float:
        if self.active <= 0 or self.allocated <= 0:
            return 0.0
        return max(0.0, (self.active - self.allocated) / self.active * 100.0)

    @property
    def resident_overhead_pct(self) -> float:
        """Resident bytes vs allocated. >2x means the allocator is
        holding ~as much physical memory as the app actually uses."""
        if self.allocated <= 0:
            return 0.0
        return (self.resident - self.allocated) / self.allocated * 100.0


def parse_jemalloc_json(text: str) -> Optional[MallocSample]:
    """Parse jemalloc's ``opts=J`` (JSON) output into a MallocSample.

    Returns None on parse failure or on a payload that doesn't look
    like jemalloc — the caller can fall back to "no allocator stats"
    rendering. Never raises.
    """
    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        return None
    je = doc.get("jemalloc")
    if not isinstance(je, dict):
        return None
    stats = je.get("stats")
    if not isinstance(stats, dict):
        return None
    sample = MallocSample(
        ts=time.time(),
        allocator="jemalloc",
        version=str(je.get("version", "")),
        allocated=int(stats.get("allocated", 0) or 0),
        active=int(stats.get("active", 0) or 0),
        metadata=int(stats.get("metadata", 0) or 0),
        resident=int(stats.get("resident", 0) or 0),
        mapped=int(stats.get("mapped", 0) or 0),
        retained=int(stats.get("retained", 0) or 0),
    )
    arenas = je.get("stats.arenas")
    if isinstance(arenas, dict):
        narenas = arenas.get("narenas")
        if isinstance(narenas, int):
            sample.narenas = narenas
    return sample


def parse_mallocstats(text: str) -> Optional[MallocSample]:
    """Top-level dispatcher. Detects allocator from the payload shape
    and parses with the appropriate format.

    Currently only jemalloc JSON is parsed; mimalloc text and the
    error-shaped JSON spine returns when no allocator was detected
    fall through to None and the caller surfaces the raw text in the
    panel error field.
    """
    if not text:
        return None
    stripped = text.lstrip()
    if stripped.startswith("{") and "\"jemalloc\"" in stripped[:200]:
        return parse_jemalloc_json(text)
    return None


def _fetch_mallocstats(base_url: str) -> str:
    """Synchronous one-shot fetch. Caller runs in an executor so the
    asyncio loop isn't blocked. Returns the response body (possibly
    empty) — never raises on transport errors; surfaces them as a
    short error string the caller can stash in the snapshot's
    ``error`` field.
    """
    url = base_url.rstrip("/") + MALLOCSTATS_PATH
    try:
        with urllib.request.urlopen(url, timeout=MALLOCSTATS_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return f"__MALLOCSTATS_FETCH_ERROR__: {type(e).__name__}: {e}"
