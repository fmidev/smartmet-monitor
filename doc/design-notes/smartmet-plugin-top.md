# `smartmet-plugin-top` — in-process dashboard plugin

**Status.** Design analysis only — not implemented. Last revised
2026-05-05. Captured here as a refresher in case the conversation
resumes; not linked from the README.

This file lives outside the install set: it ships in the git
repository but is not packaged into the RPM. The `doc/design-notes/`
directory exists for this kind of forward-looking analysis that
may or may not become work.

## The operator question

`smwebmon` today needs operator setup before its most novel features
work: either lower `kernel.perf_event_paranoid` system-wide, or grant
the unit `CAP_SYS_ADMIN` (and `CAP_BPF`) via a systemd drop-in, plus
pre-load `kheaders` for bcc-tools modes. That's a non-trivial bar to
clear at sites that don't already deploy with privileged monitoring.

The design question: would a `smartmet-plugin-top` shipped as a
SmartMet plugin (loaded into `smartmetd` itself, password-protected
like the admin plugin, JS assets served from a configurable
directory) make sense as a zero-privilege alternative? What would
have to be cut?

## Short answer

Yes, as a **complement** to `smwebmon`, not a replacement. About
70 % of the dashboard ports cleanly to a no-privilege C++ plugin;
the remaining 30 % (every flame mode, perfstat, host-wide eBPF) has
to go. Whether the trade is worth it depends on whether
zero-privilege deployment matters more than the kernel-level
diagnostics that are the current dashboard's most novel feature.

## What survives without elevated rights

Running inside `smartmetd` (user `smartmet-server`, default sysctls,
no caps), these panels keep working unchanged:

| Panel | Source | Notes |
|---|---|---|
| Overview / Plugins / URLs / API Keys | Access-log tail + admin data | Pure parsing |
| Caches / Services / Active | Already in-process | Plugin reads `Spine::Reactor` and the engines directly — no HTTP round-trip to `:8081/admin` like webmon does today |
| Logs | `tail -F` of `/var/log/smartmet/*-access-log` | Same-user reads, no ACLs |
| Network | `/proc/net/{tcp,tcp6,sockstat,netstat}`, `/proc/net/dev`, `/sys/class/net/…` | All world-readable |
| Proc (most of it) | `/proc/self/*`, `/proc/<smartmetd-PID>/{status,io,smaps_rollup,fd,task}` | Same-user, fully readable: RSS split, anon/file/shmem, `VmPTE`, swap, IO, threads, fds, page-fault counters |
| Cluster mode | HTTP fanout to peer plugins | Same model as today, just plugin-to-plugin |

## What must be removed

Every panel feature that depends on a `perf_event_open` with kernel
events or on eBPF has to go. At the RHEL default
`kernel.perf_event_paranoid=2`:

- **All flame modes that touch the kernel** — off-CPU
  (`offcputime-bpfcc`), off-CPU-locks, page-fault
  (`-e major-faults`), wakeup (`-e sched:sched_wakeup`), block-I/O
  (`-e block:block_rq_issue`), malloc (`bpftrace` uprobe).
- **perfstat** (IPC, cache-miss, branch-miss) — uses hardware
  counters via `perf_event_open`, blocked at paranoid=2 for
  non-self targets and capped to user-space even for self.
- **biolatency / runqlat** — host-wide tracepoints, not own-process
  events. Always need `CAP_BPF`.
- **Top symbols / perf-top** — `perf record` of a process the user
  owns is allowed at paranoid=2, but the dashboard's value is in
  the kernel-stack split (`__schedule`, `do_page_fault`,
  `submit_bio`), which is gated.

Worth checking experimentally before cutting it: **on-CPU user-only
self-sampling**. At paranoid=2 a process can profile its own threads
in user-space without `CAP_PERFMON`. If the dashboard is willing to
live with no kernel stacks, that one flame mode might survive.
Everything else genuinely needs the cap or the sysctl.

## Architectural sketch

Following the patterns already in `brainstorm/plugins/admin/`:

- **Auth** — copy the admin plugin's libconfig `user` + `password`
  + Basic-auth realm pattern (see
  `brainstorm/plugins/admin/admin/Plugin.cpp:77-89`). One liner; same
  operator UX as `?what=…`.
- **Routing** — register `/top` (or wherever) with `Spine::Reactor`.
  Sub-paths: `/top/` for HTML, `/top/api/<panel>` for JSON,
  `/top/assets/*` for static.
- **Static assets** — libconfig key `assetdir =
  "/usr/share/smartmet/top"`. Plugin streams the file with the right
  `Content-Type`. Configurable so dev installs can point at a
  checkout. Same approach the WMS and grid-gui plugins use for their
  templates.
- **In-process data access** — the plugin can call directly into the
  engines (`querydata`, `geonames`, etc.) and into `Spine::Reactor`'s
  `Stat` instances for cache/service/active-request data. No HTTP
  round-trip to `:8081/admin` like webmon does today; this is
  actually a small efficiency win.
- **Log ingest** — the part that needs real porting work.
  `smartmet_top.sources.logtail` and `smartmet_top.state.store` (the
  `Histogram`-per-minute-bucket per-URL retention) are about ~1 kLoC
  of Python. Faithful C++ port is a couple of weeks; faster if rotation
  detection by inode is replaced with simple `stat` polling.
- **Reuse the JS** — the dashboard's frontend in
  `share/smartmet/webmon/` is plain HTML + JS + Canvas, no build
  step. It already speaks an HTTP+JSON contract with the Python
  backend; matching that contract from a C++ handler is
  straightforward. Most of the JS works unchanged — only the
  endpoints with no privilege-free implementation
  (`/api/flame/*`, `/api/perfstat`, `/api/biolat`, `/api/runqlat`)
  have to be hidden in the UI.

## Risks and caveats

1. **Crash isolation goes away.** A bug in the plugin crashes
   `smartmetd`. Webmon's separate-process model means a parser bug
   or a runaway loop only takes down the dashboard. For a tool whose
   job is to be useful when things go wrong, that's a real
   downgrade. A plugin needs noticeably more conservative coding
   (no exceptions escaping handlers, bounded buffers, hard CPU /
   memory caps inside the handler).
2. **The C++ port is real work.** The Python is small (~3 kLoC for
   the data pipeline), but it's the part that's been iterated on
   most. You'd be re-debugging URL parsing, percentile estimation,
   rotation handling, and admin-snapshot ring sizing in a second
   language.
3. **Operator confusion if both ship.** If `smwebmon` and
   `smartmet-plugin-top` coexist, the docs need a clear "use this
   one when…" — otherwise sites end up running both for the wrong
   reasons.
4. **Cluster Proc panel still needs per-backend agents.** The plugin
   solves only single-host, no-flame monitoring. Cross-host kernel
   state (`/proc/PID/io`, page-faults per backend) still requires
   either each backend running the same plugin (fine — same RPM
   everywhere) or smwebmon-on-each-backend (current design).

## Recommendation

If the goal is **"give every SmartMet operator a dashboard with
zero privilege escalation"** — yes, build it. The subset that
survives is genuinely the core operator workflow (URLs / Plugins /
Caches / Services / Active / Logs / Network / self-Proc / cluster
fanout), and an integrated plugin is significantly easier to deploy
than systemd-unit-with-CAP-grants.

If the goal is **"replace smwebmon"** — no. The flame and perf
features are why operators reach for the dashboard during incidents;
cutting them turns it into a glorified `bstat` GUI.

A reasonable plan: ship `smartmet-plugin-top` as the default
"always available" view, keep `smwebmon` as the privileged
diagnostic tool for when an operator is willing to grant the caps.
The doc story is "plugin for routine, smwebmon when you need
flames."
