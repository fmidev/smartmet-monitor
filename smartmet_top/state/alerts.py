"""Cross-panel alert model.

A small set of detectors watches each metric source for trouble
patterns and raises `Alert` records into the central Store. Alerts
carry both a one-line title (panel-banner-friendly) and a multi-line
detail body that explains what was detected, what likely caused it,
and which other panel to look at next — the same Brendan-Gregg-style
content the README's "Reading the live monitors" chapter has, but
inline at the moment of trouble so an on-call operator does not have
to leave smtop to make sense of what it is showing them.

Lifecycle:

  * A detector fires while its condition holds, calling
    `store.upsert_alert(alert)` each cycle. The first call inserts
    the alert with `raised_ts = last_seen_ts = now`; subsequent
    calls bump `last_seen_ts` only.
  * `store.gc_alerts()` drops alerts whose `last_seen_ts` is older
    than `STALE_AFTER_SECONDS`. After that, the same condition
    firing again creates a fresh alert.
  * The operator can dismiss an alert via the `!`-overlay or the
    per-panel banner. Dismissal sticks for the lifetime of THIS
    alert: the detector keeps upserting and the alert keeps
    advancing its last_seen_ts, but UI surfaces (badge, banner,
    overlay) treat it as silent. After the auto-GC drops the
    alert, the next firing creates a fresh non-dismissed one,
    so the operator gets re-notified if the condition recurs
    after a quiet period.

Detector identities are short kebab-case strings ("majflt",
"biolat-slow", "runqlat-stalls", "perfstat-low-ipc",
"netstats-retrans", "netstats-listen-drops") that are also used as
README anchors so the overlay can deep-link.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


# Severity ordering — used by the badge to pick the colour and by
# the overlay to sort. Higher is more severe.
SEVERITIES = {"info": 0, "warn": 1, "crit": 2}


# Detector cycle timer: how long an alert can go without being
# re-fired before we consider its condition resolved and drop it.
# Detectors run on every metric cycle (5 s for biolat / runqlat,
# ~2 s for proc, 10 s for perfstat), so 60 s comfortably accommodates
# the slowest sampler missing two cycles before the alert clears.
STALE_AFTER_SECONDS = 60.0


@dataclass
class Alert:
    """One operational anomaly detected from a metric source.

    Identity is `id`: detectors that re-fire while the condition
    persists should reuse the same id so the alert is updated in
    place rather than multiplied.

    `viewed` and `dismissed` are distinct on purpose:

      * `viewed`     — the operator has acknowledged the alert
                       exists (opened the `!` overlay or noticed
                       the global strip and dismissed it). Cleared
                       only via the `mark_alerts_viewed()` Store
                       helper. While False, the global "new alert"
                       strip blinks at the top of the screen.
      * `dismissed`  — the operator has actively decided to ignore
                       this alert. Cleared only by auto-GC dropping
                       the alert and a subsequent re-firing. While
                       True, the alert is silent in every UI
                       surface but the metric is still being
                       detected.
    """

    id: str
    severity: str                 # 'info' | 'warn' | 'crit'
    detector: str                 # short identifier, README anchor source
    title: str                    # one-line, panel-banner-friendly
    detail: str                   # multi-line: detected / cause / next
    suggested_panel: Optional[str] = None    # mnemonic letter
    suggested_action: Optional[str] = None   # short imperative
    docs_anchor: Optional[str] = None        # README anchor href
    raised_ts: float = 0.0
    last_seen_ts: float = 0.0
    dismissed: bool = False
    viewed: bool = False

    def age_seconds(self, now: Optional[float] = None) -> float:
        return (now if now is not None else time.time()) - self.raised_ts

    def severity_rank(self) -> int:
        return SEVERITIES.get(self.severity, 0)
