"""Symbol-based stack filters shared by the TUI and the offline bperf tool.

Two filters are exposed:

  * `collapse_to_smartmet(stack)` — keep only SmartMet frames, plus at
    most one non-SmartMet leaf (the syscall / libc the SmartMet code
    is calling into). Any non-SmartMet frame between two SmartMet
    frames is dropped: the resulting stack is a SmartMet → SmartMet →
    … → libc/syscall chain. Returns None when the stack contains no
    SmartMet frames at all (caller can drop it).

  * `is_request_stack(stack)` — True when the stack contains spine's
    canonical request-entry symbol (`SmartMetPlugin::callRequestHandler`
    or its mangled prefix). Used to split samples into "request
    handling" vs "background" without relying on thread names.

Both filters operate on the symbol-string tuples produced by perftop's
parser, so no DSO information is required. "SmartMet code" is recognised
by symbol prefix — see `_SMARTMET_NAMESPACE_PREFIXES` and `_NFMI_CLASS_RE`
below for the full list. The previous version only matched the
`SmartMet::` namespace, which dropped the entire flame for stacks rooted
in `Fmi::`, `Imagine::`, `NFmiArea::`, etc., even though those are all
part of the SmartMet codebase. Surveyed 2026-05-04 across ~/hub.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


# Canonical entry symbol for HTTP request handling. Every plugin's
# request reaches its handler through SmartMetPlugin::callRequestHandler
# (defined in spine/SmartMetPlugin.cpp). A stack containing this frame
# is, by definition, a request-handling stack; its absence means the
# sample landed on a background thread (cleanup, scheduler, cache
# eviction, etc).
REQUEST_ENTRY_SUBSTR = "callRequestHandler"


# Demangled namespace prefixes that mark a frame as SmartMet code.
# Order doesn't matter — `startswith` returns on first match. fmitools /
# qdtools-specific namespaces (FMI, DataTransform, RadContour, TimeTools,
# WRFData, HDF5) are deliberately excluded: those are CLI-tool code that
# doesn't run inside smartmetd, so including them would only false-match
# at-most rare rare cases and confuse the hot-path interpretation.
_SMARTMET_NAMESPACE_PREFIXES = (
    # Core
    "SmartMet::",
    "Fmi::",
    "Imagine::",
    "Giza::",
    "Locus::",
    "Trax::",
    "Osm::",
    # grid-files
    "GRIB1::", "GRIB2::", "NetCDF::", "QueryData::",
    "GeoTiff::", "Map::", "GRID::", "Identification::",
    # grid-content
    "ContentServer::", "DataServer::", "QueryServer::",
    "Functions::", "Lua::", "HTTP::", "Corba::",
    "SessionManagement::", "UserManagement::",
    # textgen / timeseries
    "TextGen::", "BrainStorm::",
    "Aggregator::", "OptionParsers::", "SpecialParameter::",
    "Stat::", "TimeSeries::",
    # delfoi / observations
    "Delfoi::", "FlashQuery::", "OracleUtils::", "Observation::",
    # other
    "Dynlib::",
)


# Mangled-form fallbacks for hosts where perf script can't demangle
# (e.g. c++filt missing from PATH). Modern perf demangles by default,
# so these fire rarely; only the most common SmartMet:: form is worth
# keeping in fast-path. The rest of the namespaces are left to the
# demangled match.
_SMARTMET_MANGLED_PREFIXES = (
    "_ZN8SmartMet",
)


# Legacy class-prefix convention. newbase / smarttools / imagine predate
# the SmartMet:: namespace and use `NFmi`-prefixed global classes
# (`NFmiArea`, `NFmiPoint`, `NFmiQueryData`, ...). The `[A-Z]` lookahead
# distinguishes a real class name from anything else that happens to
# start with the four characters "NFmi" — paranoid but cheap.
_NFMI_CLASS_RE = re.compile(r"^NFmi[A-Z]")


def is_smartmet_frame(sym: str) -> bool:
    if any(sym.startswith(p) for p in _SMARTMET_NAMESPACE_PREFIXES):
        return True
    if any(sym.startswith(p) for p in _SMARTMET_MANGLED_PREFIXES):
        return True
    if _NFMI_CLASS_RE.match(sym):
        return True
    return False


def collapse_to_smartmet(
    stack: Tuple[str, ...],
) -> Optional[Tuple[str, ...]]:
    """Return the SmartMet-only view of `stack` (root → leaf order).

    Walks the stack once and keeps every SmartMet frame. After the last
    SmartMet frame we keep at most one further frame as the leaf — that
    is the syscall / libc the operator wants to see ("which kernel /
    libc call is this SmartMet code making?"). Anything below that
    leaf, and any non-SmartMet frames *between* SmartMet frames, are
    dropped.

    Returns None when the stack has no SmartMet frame at all (it was a
    pure-syscall sample, e.g. the kernel idle loop) so the caller can
    discard it.
    """
    smartmet_idx = [i for i, s in enumerate(stack) if is_smartmet_frame(s)]
    if not smartmet_idx:
        return None
    last = smartmet_idx[-1]
    # Keep every SmartMet frame from root to last. Drop any non-SmartMet
    # frames that happened to be between them — those are usually STL /
    # libc trampolines that do not aid interpretation.
    kept = [stack[i] for i in smartmet_idx]
    # Append at most one leaf below the last SmartMet frame. That leaf
    # is the operator's "what is this SmartMet code calling?" answer;
    # deeper kernel frames just add noise.
    if last + 1 < len(stack):
        kept.append(stack[last + 1])
    return tuple(kept)


def is_request_stack(stack: Tuple[str, ...]) -> bool:
    """True if any frame contains the request-entry substring.

    Substring rather than equality because the symbol can appear with
    different demangler decorations across hosts (template parameters,
    `[clone .cold]` suffixes, etc.). The substring catches every
    variant we have seen in practice.
    """
    return any(REQUEST_ENTRY_SUBSTR in f for f in stack)


# Thread-class labels used by `--threads` / the panel toggle. Centralised
# here so the CLI help text and the panel footer stay in sync.
THREAD_CLASS_REQUEST = "request"
THREAD_CLASS_BACKGROUND = "background"
THREAD_CLASS_ALL = "all"
THREAD_CLASSES = (THREAD_CLASS_ALL, THREAD_CLASS_REQUEST, THREAD_CLASS_BACKGROUND)


def keep_for_thread_class(stack: Tuple[str, ...], thread_class: str) -> bool:
    """Should `stack` survive the current thread-class filter?"""
    if thread_class == THREAD_CLASS_ALL:
        return True
    is_req = is_request_stack(stack)
    if thread_class == THREAD_CLASS_REQUEST:
        return is_req
    if thread_class == THREAD_CLASS_BACKGROUND:
        return not is_req
    return True
