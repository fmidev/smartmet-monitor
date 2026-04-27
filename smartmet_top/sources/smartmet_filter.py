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
    handling" vs "background" without relying on thread names —
    smartmetd does not call pthread_setname_np, so every thread shows
    comm=smartmetd and comm-based filtering is useless.

Both filters operate on the symbol-string tuples produced by perftop's
parser, so no DSO information is required. The heuristic for a SmartMet
frame is symbol-prefix only (`SmartMet::` namespace) — that catches all
library / engine / plugin code without needing a DSO list.
"""

from __future__ import annotations

from typing import Optional, Tuple


# Canonical entry symbol for HTTP request handling. Every plugin's
# request reaches its handler through SmartMetPlugin::callRequestHandler
# (defined in spine/SmartMetPlugin.cpp). A stack containing this frame
# is, by definition, a request-handling stack; its absence means the
# sample landed on a background thread (cleanup, scheduler, cache
# eviction, etc).
REQUEST_ENTRY_SUBSTR = "callRequestHandler"


# Prefixes / substrings that mark a frame as SmartMet code.
#
# `SmartMet::` covers the demangled namespace used by every library,
# engine, and plugin. We also accept the `_ZN8SmartMet` mangled form
# in case perf script emits mangled names on a host without c++filt
# in PATH — it shouldn't, but the fallback is cheap.
_SMARTMET_PREFIXES = ("SmartMet::", "_ZN8SmartMet")


def is_smartmet_frame(sym: str) -> bool:
    return any(sym.startswith(p) for p in _SMARTMET_PREFIXES)


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
