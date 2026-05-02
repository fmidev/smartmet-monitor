"""Flame-graph anti-pattern analyser.

Produces a ranked list of `Finding` records by scanning the retained
stack rings for a small, curated set of SmartMet-relevant performance
anti-patterns. Designed to be invoked synchronously from the Flame
panel's `a` (analyze) overlay against frozen rings — no I/O, no
subprocesses, no allocator pressure.

Detectors are intentionally heuristic: each one is a substring-pattern
match on already-collected frames. The output is "this is suspect, here
is the evidence stack" — never "this is a confirmed bug". The panel
links the operator to the evidence (mode + cursor path) so they can
inspect the flame around the finding before changing code.

Detectors v1 (operator-validated as worth shipping out of the box):

  1. locale-lock on stream construction        (off-CPU)
  2. per-request regex compile                  (on-CPU)
  3. per-request DNS                            (on-CPU)
  4. per-request GDAL/PROJ init                 (on-CPU)
  5. lock-holder/waiter pair                    (off-CPU + wakeup)
  6. major-fault working-set pressure           (pagefault)

Compute-bound-with-accidental-complexity detection was deliberately
left out: telling "Trax::contour" (legitimately hot) from
"loop_using_list_size_pre_cxx11" (accidental O(N²)) needs source
analysis, not flame data — and that's clang-tidy's job, run before
code lands. See the design conversation in the v26.5.2-14 commit
message for the full rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from .smartmet_filter import is_request_stack


# ---- Severity thresholds --------------------------------------------------
#
# Each detector uses a (high, med, low) triple of share-percentages.
# Share is detector-specific: count-share for sample-counted modes
# (on-CPU, pagefault, wakeup, blockflame), us-share for off-CPU, and
# byte-share for malloc. The thresholds reflect how surprising a hit
# in that mode is — DNS in a request path at 1% is already alarming;
# major faults at 1% are background noise.

SEV_HIGH = "high"
SEV_MED = "med"
SEV_LOW = "low"
_SEVERITIES = (SEV_HIGH, SEV_MED, SEV_LOW)


def _severity(share: float, high: float, med: float, low: float) -> Optional[str]:
    """Bucket `share` (a fraction in 0..1) by the three thresholds.
    Returns None when the share is below the low threshold so the
    detector can suppress the finding entirely."""
    if share >= high:
        return SEV_HIGH
    if share >= med:
        return SEV_MED
    if share >= low:
        return SEV_LOW
    return None


# ---- Finding --------------------------------------------------------------

@dataclass
class Finding:
    """One analyser hit, ready for the Flame panel to render and link to.

    `mode` is the flame mode that owns the evidence — pressing Enter on
    the finding switches the panel to that mode and places the cursor
    on `evidence_stack`. `share_pct` is the percentage of the ring's
    weight (count / µs / bytes, depending on mode) that the matched
    pattern accounts for. `hint` is the one-line "what to do" prompt.
    """

    detector_id: str
    severity: str
    title: str
    share_pct: float
    mode: str
    evidence_stack: Tuple[str, ...]
    hint: str

    def severity_rank(self) -> int:
        """For sorting: high first, low last."""
        try:
            return _SEVERITIES.index(self.severity)
        except ValueError:
            return len(_SEVERITIES)


# ---- Stack-matching primitives -------------------------------------------

def _stack_contains_any(stack: Tuple[str, ...],
                        substrings: Sequence[str]) -> bool:
    """True if any frame in `stack` contains any of `substrings`.
    Substring rather than prefix because demangled symbol names carry
    template parameters and clone suffixes that prefix-matching trips on."""
    return any(any(s in f for s in substrings) for f in stack)


def _leaf_contains_any(stack: Tuple[str, ...],
                       substrings: Sequence[str]) -> bool:
    """Variant of _stack_contains_any restricted to the leaf frame.
    Used for lock-class detection where the *kind* of off-CPU event
    is determined by the kernel function the thread parked in."""
    if not stack:
        return False
    leaf = stack[-1]
    return any(s in leaf for s in substrings)


# Lock-leaf substrings, mirrored from panels/flame.py:_LOCK_LEAF_PATTERNS.
# Duplicated rather than imported because the panel module pulls in
# curses on import; keeping this module curses-free lets the analyser
# stay testable from `make check`.
_LOCK_LEAF_PATTERNS = (
    "futex_wait", "futex_q", "do_futex",
    "__lll_lock", "__lll_unlock",
    "pthread_mutex", "pthread_cond",
    "pthread_rwlock", "pthread_spin",
    "__pthread_mutex", "__pthread_cond",
)


def _is_lock_stack(stack: Tuple[str, ...]) -> bool:
    return _leaf_contains_any(stack, _LOCK_LEAF_PATTERNS)


def _smartmet_ancestors(stack: Tuple[str, ...]) -> List[str]:
    """SmartMet frames in `stack`, root → leaf order. Used by the
    lock-pair detector to look for the same SmartMet function on both
    sides of a futex pair."""
    return [f for f in stack if "SmartMet::" in f]


# ---- Detector helpers ----------------------------------------------------

def _pick_evidence_count(matches: Iterable[Tuple[str, ...]]) -> Tuple[str, ...]:
    """Pick the most-frequent stack from `matches` to serve as the
    finding's evidence. For sample-counted detectors only."""
    best: Tuple[str, ...] = ()
    best_n = 0
    counts: dict = {}
    for stack in matches:
        counts[stack] = counts.get(stack, 0) + 1
    for stack, n in counts.items():
        if n > best_n:
            best, best_n = stack, n
    return best


def _pick_evidence_weighted(
    matches: Iterable[Tuple[Tuple[str, ...], int]],
) -> Tuple[str, ...]:
    """Pick the heaviest-weighted stack from a (stack, weight) iterable."""
    best: Tuple[str, ...] = ()
    best_w = 0
    sums: dict = {}
    for stack, w in matches:
        sums[stack] = sums.get(stack, 0) + w
    for stack, w in sums.items():
        if w > best_w:
            best, best_w = stack, w
    return best


# ---- Detectors -----------------------------------------------------------

# Patterns known to drag std::locale's global mutex onto the off-CPU
# path: std::ios_base::Init runs once per stream construction and walks
# the locale facets under a shared lock; boost::lexical_cast is the
# textbook offender because it hides this path inside a single-line
# call.
_LOCALE_PATTERNS = (
    "std::locale::id::_M_id",
    "std::__use_facet", "std::use_facet",
    "basic_stringstream", "basic_ostringstream", "basic_istringstream",
    "boost::lexical_cast",
    "std::ios_base::Init",
    "_M_init_streambuf",
)


def _detect_locale_lock(store, pid: int) -> Optional[Finding]:
    weighted = store.offcpu_recent_stacks(pid)
    if not weighted:
        return None
    matched_us = 0
    total_us = 0
    matches: List[Tuple[Tuple[str, ...], int]] = []
    for stack, us in weighted:
        if us <= 0:
            continue
        total_us += us
        if _is_lock_stack(stack) and _stack_contains_any(stack, _LOCALE_PATTERNS):
            matched_us += us
            matches.append((stack, us))
    if total_us == 0 or matched_us == 0:
        return None
    share = matched_us / total_us
    sev = _severity(share, high=0.30, med=0.15, low=0.05)
    if sev is None:
        return None
    return Finding(
        detector_id="locale-lock",
        severity=sev,
        title="locale lock on stream construction",
        share_pct=share * 100,
        mode="off-cpu-locks",
        evidence_stack=_pick_evidence_weighted(matches),
        hint=("std::locale::id holds a global mutex; constructing a "
              "stringstream / ostringstream / lexical_cast acquires it "
              "every time. Reuse one stream with imbue(locale::classic()) "
              "or switch to fmt::format / std::to_chars (no locale path)."),
    )


_REGEX_PATTERNS = (
    "std::__detail::_Compiler", "_Compiler::_M_compile",
    "std::regex::assign", "std::__cxx11::basic_regex",
    "std::__regex_compile",
    "boost::re_detail", "boost::regex_traits",
    "regex_compile",
)


def _detect_request_regex_compile(store, pid: int) -> Optional[Finding]:
    stacks = store.perf_recent_stacks(pid)
    if not stacks:
        return None
    request_stacks = [s for s in stacks if is_request_stack(s)]
    if not request_stacks:
        return None
    matches = [s for s in request_stacks
               if _stack_contains_any(s, _REGEX_PATTERNS)]
    if not matches:
        return None
    share = len(matches) / len(request_stacks)
    sev = _severity(share, high=0.10, med=0.05, low=0.02)
    if sev is None:
        return None
    return Finding(
        detector_id="request-regex-compile",
        severity=sev,
        title="regex compiled per request",
        share_pct=share * 100,
        mode="on-cpu",
        evidence_stack=_pick_evidence_count(matches),
        hint=("compiling std::regex / boost::regex on every call costs "
              "O(pattern length) per request. Construct the regex once "
              "(static const, or as a class member) and reuse it."),
    )


_DNS_PATTERNS = (
    "getaddrinfo", "__GI_getaddrinfo",
    "gethostbyname", "__nss_lookup",
    "_nss_dns_gethostbyname",
    "res_send", "res_query",
)


def _detect_request_dns(store, pid: int) -> Optional[Finding]:
    """DNS in a request path is almost always a bug. We check both
    on-CPU (fast path) and off-CPU (the resolver is blocking on a
    socket) and report whichever share is higher."""
    on_stacks = store.perf_recent_stacks(pid)
    off_weighted = store.offcpu_recent_stacks(pid)

    on_request = [s for s in on_stacks if is_request_stack(s)] if on_stacks else []
    on_matches = [s for s in on_request
                  if _stack_contains_any(s, _DNS_PATTERNS)]
    on_share = (len(on_matches) / len(on_request)) if on_request else 0.0

    off_total = 0
    off_match_us = 0
    off_matches: List[Tuple[Tuple[str, ...], int]] = []
    for stack, us in off_weighted:
        if us <= 0 or not is_request_stack(stack):
            continue
        off_total += us
        if _stack_contains_any(stack, _DNS_PATTERNS):
            off_match_us += us
            off_matches.append((stack, us))
    off_share = (off_match_us / off_total) if off_total else 0.0

    if on_share == 0 and off_share == 0:
        return None
    # Prefer the off-CPU view when it's the heavier signal — the
    # operator wants to see *where the thread parked* in DNS, not the
    # thin on-CPU sliver that bookends it.
    if off_share >= on_share and off_matches:
        share = off_share
        evidence = _pick_evidence_weighted(off_matches)
        mode = "off-cpu"
    else:
        share = on_share
        evidence = _pick_evidence_count(on_matches)
        mode = "on-cpu"
    sev = _severity(share, high=0.01, med=0.003, low=0.001)
    if sev is None:
        return None
    return Finding(
        detector_id="request-dns",
        severity=sev,
        title="DNS lookup in request path",
        share_pct=share * 100,
        mode=mode,
        evidence_stack=evidence,
        hint=("getaddrinfo on the request path means the resolver is "
              "queried per call. Pre-resolve at startup, cache results, "
              "or run nscd / systemd-resolved to absorb the cost."),
    )


_GDAL_INIT_PATTERNS = (
    "OGRRegisterAll", "GDALAllRegister",
    "OGRRegisterAllInternal",
    "pj_init", "pj_create", "proj_create",
    "OSRImportFromEPSG", "OSRImportFromWkt",
    "OGRSpatialReference::importFromEPSG",
)


def _detect_request_gdal_init(store, pid: int) -> Optional[Finding]:
    stacks = store.perf_recent_stacks(pid)
    if not stacks:
        return None
    request_stacks = [s for s in stacks if is_request_stack(s)]
    if not request_stacks:
        return None
    matches = [s for s in request_stacks
               if _stack_contains_any(s, _GDAL_INIT_PATTERNS)]
    if not matches:
        return None
    share = len(matches) / len(request_stacks)
    sev = _severity(share, high=0.02, med=0.005, low=0.001)
    if sev is None:
        return None
    return Finding(
        detector_id="request-gdal-init",
        severity=sev,
        title="GDAL/PROJ initialisation in request path",
        share_pct=share * 100,
        mode="on-cpu",
        evidence_stack=_pick_evidence_count(matches),
        hint=("OGRRegisterAll / pj_create / OSRImportFromEPSG re-parses "
              "EPSG tables and driver registrations every time. Initialise "
              "GDAL/PROJ once at engine/plugin startup and cache the "
              "OGRSpatialReference / PJ handles."),
    )


def _detect_lock_pair(store, pid: int) -> Optional[Finding]:
    """Find a SmartMet function that appears on both the off-CPU
    (lock-waiter) and wakeup (lock-holder) sides — the contention
    pair. The heuristic is: the same SmartMet ancestor in both rings
    means the function is both blocking on AND releasing the same
    lock under load."""
    weighted = store.offcpu_recent_stacks(pid)
    wakeups = store.wakeup_recent_stacks(pid)
    if not weighted or not wakeups:
        return None

    # Tally µs blocked per SmartMet ancestor on the waiter side, but
    # only over lock-leaf stacks (futex/mutex). We weight by the
    # innermost SmartMet frame (the one that actually called into the
    # lock primitive); deeper frames are usually generic helpers.
    waiter_us: dict = {}
    waiter_evidence: dict = {}
    total_lock_us = 0
    for stack, us in weighted:
        if us <= 0 or not _is_lock_stack(stack):
            continue
        total_lock_us += us
        ancestors = _smartmet_ancestors(stack)
        if not ancestors:
            continue
        innermost = ancestors[-1]
        waiter_us[innermost] = waiter_us.get(innermost, 0) + us
        # Keep the heaviest example as evidence.
        prev = waiter_evidence.get(innermost)
        if prev is None or prev[1] < us:
            waiter_evidence[innermost] = (stack, us)
    if total_lock_us == 0 or not waiter_us:
        return None

    # Same on the wakeup side — look for the same SmartMet frame as
    # the innermost ancestor of any wakeup stack.
    waker_funcs = set()
    for stack in wakeups:
        for a in _smartmet_ancestors(stack):
            waker_funcs.add(a)
    if not waker_funcs:
        return None

    # Best pair = SmartMet ancestor that appears as both a heavy
    # waiter and somewhere in the wakeup ring.
    best_func = None
    best_us = 0
    for func, us in waiter_us.items():
        if func in waker_funcs and us > best_us:
            best_func, best_us = func, us
    if best_func is None:
        return None

    share = best_us / total_lock_us
    sev = _severity(share, high=0.10, med=0.03, low=0.01)
    if sev is None:
        return None
    evidence = waiter_evidence[best_func][0]
    return Finding(
        detector_id="lock-pair",
        severity=sev,
        title=f"lock contention pair: {best_func}",
        share_pct=share * 100,
        mode="off-cpu-locks",
        evidence_stack=evidence,
        hint=("the same SmartMet function appears as both lock-waiter "
              "(off-CPU) and lock-holder (wakeup). Shrink the critical "
              "section, switch to std::shared_mutex if read-heavy, or "
              "shard the lock by a stable key."),
    )


_FILEMAP_PATTERNS = (
    "filemap_fault", "do_read_fault", "__do_fault",
    "pagecache_get_page", "find_get_page",
    "do_sync_mmap_readahead",
)


def _detect_major_faults(store, pid: int) -> Optional[Finding]:
    stacks = store.pagefault_recent_stacks(pid)
    if not stacks:
        return None
    matches = [s for s in stacks
               if _stack_contains_any(s, _FILEMAP_PATTERNS)]
    if not matches:
        return None
    share = len(matches) / len(stacks)
    sev = _severity(share, high=0.50, med=0.25, low=0.10)
    if sev is None:
        return None
    return Finding(
        detector_id="major-fault-working-set",
        severity=sev,
        title="major faults dominated by file-backed mmap",
        share_pct=share * 100,
        mode="pagefault",
        evidence_stack=_pick_evidence_count(matches),
        hint=("the working set exceeds RAM — mmap'd querydata / GRIB "
              "files are paged in synchronously per request. Pre-warm "
              "with `vmtouch -t`, add RAM, or split data so the hot "
              "subset fits."),
    )


# ---- Orchestrator --------------------------------------------------------

_DETECTORS = (
    _detect_locale_lock,
    _detect_request_regex_compile,
    _detect_request_dns,
    _detect_request_gdal_init,
    _detect_lock_pair,
    _detect_major_faults,
)


def analyze(store, pid: int) -> List[Finding]:
    """Run every detector against the rings owned by `pid` and return
    the findings sorted by severity (high → low) and share descending.
    Detectors that can't fire (missing ring, zero matches, sub-threshold
    share) return None and are filtered out."""
    findings: List[Finding] = []
    for det in _DETECTORS:
        try:
            f = det(store, pid)
        except Exception:
            # Detectors are heuristics over operator-visible data; a
            # malformed stack must not take the analyser down. Swallow
            # and continue — the missing finding is the price of safety.
            continue
        if f is not None:
            findings.append(f)
    findings.sort(key=lambda f: (f.severity_rank(), -f.share_pct))
    return findings
