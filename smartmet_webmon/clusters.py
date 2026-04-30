"""Multi-cluster support for smwebmon.

Each cluster is a single SmartMet frontend (`smartmet.fmi.fi`,
`opendata.fmi.fi`, ...) plus the backends it routes to. The
registry holds one ``Store`` + per-backend polling-task graph
per configured cluster; the cluster selector in the dashboard
picks which store the panels render.

Discovery happens by parsing the frontend's
``/info?what=clusterinfo`` HTML (a Sputnik render — there is no
JSON form). The parser is deliberately small: a one-pass state
machine over the indent structure, picking out single-segment
URI prefixes and their handler bodies.

Shared back-end / front-end terminology in this module:
    * **prefix**       — the routing label for a backend, e.g. "c2"
                         or "v1.q3". Comes verbatim from the frontend's
                         clusterinfo HTML — no FQDN rewriting.
    * **admin URL**    — direct admin endpoint for one backend, e.g.
                         http://c2.back.smartmet.fmi.fi:8081/admin .
                         Constructed from the cluster config's
                         ``admin-url-pattern`` with ``{prefix}``
                         substituted in.

This module is in ``smartmet_webmon`` (not ``smartmet_top``) because
clustering is a web-only feature: the curses ``smtop`` doesn't
extend naturally to a cluster selector and the user has no need for
it interactively. ``Store`` and the source loops live in
``smartmet_top`` and stay shared.
"""

from __future__ import annotations

import asyncio
import configparser
import re
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from smartmet_top.sources.adminapi import poll_admin
from smartmet_top.state.store import Store, set_history_minutes


# ---------------------------------------------------------------------------
# clusterinfo HTML parser
# ---------------------------------------------------------------------------

# A backend prefix is a single-segment URI directory: e.g. "URI /c2/"
# or "URI /v1.q3/". The regex requires:
#   * leading whitespace (clusterinfo indents the URI lines)
#   * a single segment built from word-chars, dot, dash, underscore
#   * a mandatory trailing slash (this is what distinguishes a backend
#     prefix like "URI /c2/" from a service routing entry like
#     "URI /c2/timeseries", which has chars after the next slash)
_BACKEND_PREFIX_RE = re.compile(
    r"^\s+URI\s+/(?P<prefix>[A-Za-z0-9._-]+)/\s*$"
)

# Any line that starts a new "URI" section, used to close the previous
# section. Includes both backend prefixes (which we capture) and
# routing entries (which we don't).
_ANY_URI_LINE_RE = re.compile(r"^\s+URI\s+/")


@dataclass
class BackendInfo:
    """One backend in a cluster. Status is *alive* iff the frontend
    has any concrete handler entries listed under the backend's
    prefix in the clusterinfo HTML — that's how Sputnik signals
    "this backend is currently routable" (versus "the prefix is
    registered but the backend is offline / draining / paused")."""
    prefix: str
    alive: bool = False
    handlers: List[str] = field(default_factory=list)


def parse_clusterinfo(html_text: str) -> List[BackendInfo]:
    """Extract backend prefix list + alive/down status from
    ``/info?what=clusterinfo`` HTML.

    The Sputnik HTML render has the structure::

        URI /c2/                ← backend prefix (single-segment + slash)
            c2/                 ← handler entries (alive)
            c2/timeseries
        URI /c2/timeseries      ← service routing entry (not a backend)
            c2/timeseries
        URI /c1/                ← backend prefix
        URI /c1/timeseries      ← prefix's body is empty → c1 is DOWN

    The parser walks the lines once, switches to "in this backend's
    body" on a backend prefix, accumulates indented body lines until
    the next ``URI`` line. Identical prefixes seen multiple times
    merge into the first sighting (shouldn't happen in practice but
    cheap to handle).
    """
    backends: Dict[str, BackendInfo] = {}
    current: Optional[BackendInfo] = None

    for line in html_text.splitlines():
        if _ANY_URI_LINE_RE.match(line):
            current = None
            m = _BACKEND_PREFIX_RE.match(line)
            if m:
                prefix = m.group("prefix")
                if prefix not in backends:
                    backends[prefix] = BackendInfo(prefix=prefix)
                current = backends[prefix]
        elif current is not None:
            stripped = line.strip()
            if stripped:
                current.handlers.append(stripped)
                current.alive = True

    return list(backends.values())


# ---------------------------------------------------------------------------
# clusters.conf reader
# ---------------------------------------------------------------------------

@dataclass
class ClusterConfig:
    """One cluster's static configuration, read from the INI file
    (``[name]`` section). Missing fields raise on load — these
    are operator-required."""
    name: str
    frontend_url: str
    admin_url_pattern: str
    log_glob: str = ""
    admin_interval: float = 2.0
    discovery_interval: float = 60.0


def load_clusters_config(path: str) -> List[ClusterConfig]:
    """Read an INI-style clusters config. One section per cluster::

        [back]
        frontend-url = http://smartmet.fmi.fi
        admin-url-pattern = http://{prefix}.back.smartmet.fmi.fi:8081/admin
        # optional:
        # log-glob = /var/log/smartmet/*-access-log
        # admin-interval = 2.0
        # discovery-interval = 60.0

    The ``{prefix}`` placeholder in ``admin-url-pattern`` is
    substituted with the short label discovered in clusterinfo
    (``c2``, ``v1.q3``, etc.) — no FQDN rewriting, no port guessing.

    Returns an empty list if the file is missing (so single-host
    mode keeps working when no clusters config is shipped).
    """
    cp = configparser.ConfigParser()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cp.read_file(fh)
    except FileNotFoundError:
        return []
    out: List[ClusterConfig] = []
    for name in cp.sections():
        s = cp[name]
        frontend = s.get("frontend-url", "").strip()
        pattern = s.get("admin-url-pattern", "").strip()
        if not frontend or not pattern:
            sys.stderr.write(
                f"smwebmon: cluster '{name}' missing required "
                f"frontend-url or admin-url-pattern; skipping\n"
            )
            continue
        if "{prefix}" not in pattern:
            sys.stderr.write(
                f"smwebmon: cluster '{name}' admin-url-pattern has no "
                f"{{prefix}} placeholder; skipping\n"
            )
            continue
        out.append(ClusterConfig(
            name=name,
            frontend_url=frontend.rstrip("/"),
            admin_url_pattern=pattern,
            log_glob=s.get("log-glob", "").strip(),
            admin_interval=float(s.get("admin-interval", "2.0")),
            discovery_interval=float(s.get("discovery-interval", "60.0")),
        ))
    return out


# ---------------------------------------------------------------------------
# Discovery + per-backend polling
# ---------------------------------------------------------------------------

def _fetch_clusterinfo(frontend_url: str, timeout: float = 5.0) -> str:
    """Synchronous fetch of clusterinfo HTML. Wrapped in
    run_in_executor by the discovery loop so the asyncio event
    loop stays free."""
    url = frontend_url.rstrip("/") + "/info?what=clusterinfo"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "smartmet-webmon-discovery"},
    )
    # ProxyHandler({}) bypasses any HTTP_PROXY env (the FMI build
    # hosts have one and it intercepts loopback; same risk applies
    # to internal cluster URLs). Local sysctl on production hosts
    # may also have it set unintentionally — this defends against
    # both.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


@dataclass
class ClusterContext:
    """Live state for one cluster: store, the per-backend polling
    tasks, the most recent discovery snapshot, and the discovery
    loop task itself.

    Stored fields:
        * ``store`` — the ``smartmet_top.state.store.Store`` panels
          query for this cluster's data.
        * ``tasks`` — dict of prefix → asyncio.Task running
          ``poll_admin`` for that backend. Discovery adds/removes
          entries as backends come and go.
        * ``last_backends`` — most recent ``parse_clusterinfo`` output.
          Powers /api/cluster/topology.
        * ``discovery_status`` — short status string, surfaced via
          /api/clusters so the dashboard can show "fetching", "ok",
          or the failure reason.
    """
    config: ClusterConfig
    store: Store = field(default_factory=Store)
    tasks: Dict[str, asyncio.Task] = field(default_factory=dict)
    last_backends: List[BackendInfo] = field(default_factory=list)
    discovery_status: str = "(discovery not started)"
    discovery_task: Optional[asyncio.Task] = None


class ClusterRegistry:
    """The set of configured clusters. The web handlers look up the
    right ``Store`` here based on the ``?cluster=NAME`` query param.

    Single-host mode (no clusters configured) is represented as the
    empty registry plus a separate ``Store`` owned by the caller —
    the registry has nothing to do in that case and this whole
    module is dormant.
    """

    def __init__(self) -> None:
        self.clusters: Dict[str, ClusterContext] = {}

    def add(self, config: ClusterConfig) -> ClusterContext:
        ctx = ClusterContext(config=config)
        self.clusters[config.name] = ctx
        return ctx

    def get(self, name: str) -> Optional[ClusterContext]:
        return self.clusters.get(name)

    def names(self) -> List[str]:
        return list(self.clusters.keys())

    def __bool__(self) -> bool:
        return bool(self.clusters)

    def all(self) -> Iterable[ClusterContext]:
        return self.clusters.values()


async def discovery_loop(ctx: ClusterContext) -> None:
    """Refetches the cluster's frontend clusterinfo on a loop;
    spawns a ``poll_admin`` task per alive backend; cancels and
    re-spawns as the backend list changes.

    Empty backend list (e.g. discovery error) leaves existing
    polling tasks alone — better to keep the last good polling
    set than to teardown on a transient frontend hiccup.
    """
    loop = asyncio.get_event_loop()
    while True:
        try:
            html = await loop.run_in_executor(
                None, _fetch_clusterinfo, ctx.config.frontend_url)
            backends = parse_clusterinfo(html)
        except urllib.error.URLError as e:
            ctx.discovery_status = f"unreachable: {e.reason}"
            await asyncio.sleep(ctx.config.discovery_interval)
            continue
        except Exception as e:
            ctx.discovery_status = f"error: {type(e).__name__}: {e}"
            await asyncio.sleep(ctx.config.discovery_interval)
            continue

        ctx.last_backends = backends
        alive = {b.prefix for b in backends if b.alive}
        ctx.discovery_status = (
            f"ok ({len(alive)}/{len(backends)} alive)"
            if backends else "ok (no backends listed)"
        )

        # Spawn polling tasks for newly-alive backends.
        for prefix in alive:
            if prefix not in ctx.tasks:
                admin_url = ctx.config.admin_url_pattern.format(prefix=prefix)
                ctx.store.register_admin_host(prefix)
                ctx.tasks[prefix] = asyncio.create_task(
                    poll_admin(admin_url, prefix, ctx.store,
                                interval=ctx.config.admin_interval),
                    name=f"poll[{ctx.config.name}/{prefix}]",
                )

        # Cancel polling tasks for backends that went away (down or
        # removed from the cluster).
        for prefix in list(ctx.tasks):
            if prefix not in alive:
                t = ctx.tasks.pop(prefix)
                t.cancel()

        await asyncio.sleep(ctx.config.discovery_interval)


def start_cluster(ctx: ClusterContext, history_minutes: int) -> None:
    """Kick off a cluster's discovery loop. Per-backend polling
    tasks are spawned by the loop on demand (after the first
    successful clusterinfo fetch). No-op if already started.
    """
    set_history_minutes(history_minutes)
    if ctx.discovery_task is not None:
        return
    ctx.discovery_task = asyncio.create_task(
        discovery_loop(ctx),
        name=f"discovery[{ctx.config.name}]",
    )


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

def _detect_local_role(probe_timeout: float = 1.0
                       ) -> Optional[Tuple[str, str]]:
    """Probe localhost for a SmartMet daemon and identify whether it's
    a frontend or a backend. Returns (role, base_url) or None.

    Tries the standard FMI ports — 8080 (frontend convention) and 8081
    (backend convention) — but doesn't trust the port-to-role mapping;
    the actual role comes from parsing the clusterinfo HTML which
    self-identifies as ``This server is a FRONTEND`` / ``a BACKEND``.
    """
    for port in (8080, 8081):
        base = f"http://localhost:{port}"
        try:
            html = _fetch_clusterinfo(base, timeout=probe_timeout)
        except Exception:
            continue
        if "FRONTEND" in html:
            return ("frontend", base)
        if "BACKEND" in html:
            return ("backend", base)
    return None


def _derive_cluster_domain(local_fqdn: str
                           ) -> Optional[Tuple[str, str]]:
    """From the local FQDN derive (cluster_name, cluster_dns_domain).

    The naming convention this code assumes — and the only one that
    seems to be in use at FMI — is ``<prefix>.<cluster>.<rest>``,
    e.g. ``c3.back.smartmet.fmi.fi`` → cluster_name="back",
    cluster_dns_domain="back.smartmet.fmi.fi". Backends in the cluster
    are then reachable as ``<other-prefix>.back.smartmet.fmi.fi``.

    Returns None for hostnames that don't fit the pattern (too short,
    or single-label); operator falls back to writing clusters.conf
    by hand.
    """
    parts = local_fqdn.split(".")
    if len(parts) < 3:
        return None
    cluster_name = parts[1]
    cluster_dns = ".".join(parts[1:])
    return (cluster_name, cluster_dns)


def autodetect_cluster(probe_timeout: float = 1.0
                       ) -> Optional[ClusterConfig]:
    """If localhost runs a SmartMet frontend, build a ClusterConfig
    from its clusterinfo + the local FQDN's domain. Operator-friendly
    default for hosts that match FMI's deployment convention; sites
    that don't can still write clusters.conf by hand and the
    autodetect simply returns None.

    Returns None if:
      * No frontend on localhost (we're on a backend, an ops box, or
        a host without SmartMet)
      * Local FQDN is single-label or two-label (no cluster domain
        component to extract)
    """
    role = _detect_local_role(probe_timeout=probe_timeout)
    if role is None or role[0] != "frontend":
        return None
    frontend_base = role[1]
    derived = _derive_cluster_domain(socket.getfqdn())
    if derived is None:
        return None
    name, cluster_dns = derived
    return ClusterConfig(
        name=name,
        frontend_url=frontend_base,
        admin_url_pattern=f"http://{{prefix}}.{cluster_dns}:8081/admin",
    )


# ---------------------------------------------------------------------------
# Discovery + per-backend polling (continued)
# ---------------------------------------------------------------------------

def stop_cluster(ctx: ClusterContext) -> None:
    """Cancel discovery + all per-backend polling tasks for a
    cluster. Used at shutdown (and a future "remove cluster at
    runtime" feature)."""
    if ctx.discovery_task is not None:
        ctx.discovery_task.cancel()
        ctx.discovery_task = None
    for t in list(ctx.tasks.values()):
        t.cancel()
    ctx.tasks.clear()
