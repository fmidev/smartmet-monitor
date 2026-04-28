"""Locate the on-disk asset root (HTML/JS/CSS).

The web binary needs to find ``share/smartmet/webmon/`` whether it's
running from the source tree (``./smwebmon`` next to the repo) or
installed into ``/usr`` by the RPM. Mirrors the lookup pattern in
``share/smartmet/bstat.sh``.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

# Search order: env override → source-tree sibling → packaged path.
_DEFAULT_PATHS: tuple = (
    "/usr/share/smartmet/webmon",
    "/usr/local/share/smartmet/webmon",
)


def candidate_paths(extra: Iterable[str] = ()) -> list:
    """Yield asset-root candidates in the order they should be tried."""
    out = []
    env = os.environ.get("SMARTMET_WEBMON_ASSETS")
    if env:
        out.append(env)
    out.extend(extra)
    here = os.path.dirname(os.path.realpath(__file__))
    out.append(os.path.normpath(os.path.join(here, "..", "share",
                                             "smartmet", "webmon")))
    out.extend(_DEFAULT_PATHS)
    seen = set()
    deduped = []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def resolve_asset_root(extra: Iterable[str] = ()) -> Optional[str]:
    """Return the first candidate path that contains ``index.html``.

    Returns ``None`` if no candidate exists, in which case the server
    will refuse to start so the operator gets a clear error rather
    than silent 404s.
    """
    for p in candidate_paths(extra):
        if os.path.isfile(os.path.join(p, "index.html")):
            return p
    return None
