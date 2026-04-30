# `kernel.perf_event_paranoid` — what it controls, what each monitor feature needs

`kernel.perf_event_paranoid` is the kernel sysctl that decides who can use
the `perf_event_open(2)` syscall family for what. Its value gates almost
every profiling feature `smtop`, `smwebmon`, and `bperf` provide. This
document is the one place operators should need to read to decide what
value their host should run with and why.

## TL;DR

| Host                                        | Recommended `kernel.perf_event_paranoid` |
|---------------------------------------------|------------------------------------------|
| Operator's interactive workstation          | `2` (the RHEL default; run `smtop --perf` via `sudo` when you want flames) |
| Production backend running `smwebmon` 24×7  | `0`                                      |
| Hardened / multi-tenant production host     | `2` + grant `smartmet-webmon.service` `CAP_SYS_ADMIN` via a drop-in |

A typical SmartMet backend that wants the full diagnostic kit
(on-CPU + off-CPU + page-fault + wakeup + block-I/O flames, IPC/cache
stats, latency histograms) needs `paranoid = 0` plus the `kheaders`
kernel module pre-loaded for `bcc-tools`. **Installing
`smartmet-webmon` (>= 26.4.30-7) does both automatically** —
`/usr/lib/sysctl.d/99-smartmet-perf.conf` sets paranoid to 0,
`/usr/lib/modules-load.d/smartmet-perf.conf` pre-loads kheaders, and
the package's `%post` applies both immediately so no reboot is
needed. Sites that can't accept these defaults should use the CLI
tools (`smtop`, `bstat`, `bperf`) from `smartmet-monitor` instead
and not install `smartmet-webmon`.

The rest of this document covers the per-feature compatibility table,
the alternatives for hosts that need a more surgical setup, and the
security trade-offs.

## What each level disallows

Four valid values. The kernel docs phrase the levels in terms of what
they *deny* to users without `CAP_PERFMON` (or, on RHEL 8 kernels that
predate `CAP_PERFMON`, `CAP_SYS_ADMIN`):

```
-1: Allow use of (almost) all events by all users.
    Ignore mlock limit after perf_event_mlock_kb without CAP_IPC_LOCK.
>= 0: Disallow ftrace function tracepoint by users without CAP_PERFMON.
      Disallow raw tracepoint access by users without CAP_PERFMON.
>= 1: Disallow CPU event access by users without CAP_PERFMON.
>= 2: Disallow kernel profiling by users without CAP_PERFMON.
```

Translated:

| Level | What unprivileged users (no CAP_PERFMON) can do                |
|------:|----------------------------------------------------------------|
| `-1`  | Everything: raw tracepoints, ftrace, CPU events, kernel profiling. |
| `0`   | CPU events (`cycles`, `instructions`, …), kernel profiling, regular tracepoints. **Raw and ftrace tracepoints denied.** |
| `1`   | Software events (`cpu-clock`, `task-clock`, `page-faults`), regular tracepoints, kernel profiling. **CPU events denied.** |
| `2`   | Software events and regular tracepoints for own processes only. **CPU events and kernel profiling denied.** Default on RHEL / Fedora. |

The "regular tracepoint" line is fuzzy and varies a little between
kernels: some tracepoints (e.g. `sched:sched_switch`) work for own
processes at `paranoid >= 1` on recent kernels, others only at `0`.

## Feature → paranoid mapping

What each panel and sampler in this project needs to function for an
unprivileged user. "✓" works, "✗" fails. (When run via `sudo` or with
the unit granted `CAP_SYS_ADMIN`, all rows become "✓" regardless of
paranoid.)

| Feature                                 | `paranoid -1` | `0` | `1` | `2` |
|-----------------------------------------|:-------------:|:---:|:---:|:---:|
| **smtop / smwebmon — Flame, on-CPU** (samples `cycles`, a hardware perf counter) | ✓ | ✓ | ✗ | ✗ |
| **smtop / smwebmon — Flame, off-CPU** (off-CPU + off-CPU-locks via `offcputime-bpfcc`) | ✓ | ✓ | ✓ | ✗ |
| **smtop / smwebmon — Flame, page-fault** | ✓           | ✓   | ✗   | ✗   |
| **smtop / smwebmon — Flame, wakeup**   | ✓             | ✓   | ✗   | ✗   |
| **smtop / smwebmon — Flame, block-I/O** | ✓            | ✓   | ✗   | ✗   |
| **smtop / smwebmon — Flame, malloc**   | ✓             | ✓   | ✗   | ✗   |
| **smtop / smwebmon — Proc, biolat (block-I/O latency histogram)** | ✓ | ✓ | ✓ | ✓ |
| **smtop / smwebmon — Proc, runqlat (run-queue latency)** | ✓ | ✓ | ✓ | ✓ |
| **smtop / smwebmon — Proc, perfstat (IPC + cache + branch miss rate)** | ✓ | ✓ | ✗ | ✗ |
| **smtop / smwebmon — Proc panel (memory / IO / threads / fds)** | ✓ | ✓ | ✓ | ✓ |
| **smtop / smwebmon — URLs / Plugins / Caches / Services / Active / Keys** | ✓ | ✓ | ✓ | ✓ |
| **smtop / smwebmon — Network panel** | ✓               | ✓   | ✓   | ✓   |
| **smtop / smwebmon — Logs panel**    | ✓               | ✓   | ✓   | ✓   |
| **bperf (offline profile capture)**   | ✓               | ✓   | ✗   | ✗   |

A planned refinement (not yet shipped) is to switch the on-CPU sampler
from the `cycles` hardware event to the `cpu-clock` software event when
running unprivileged. `cpu-clock` is allowed at every paranoid level for
own-uid processes, so the on-CPU flame would work at the RHEL default
without any operator action. The two profiles are visually
indistinguishable for hot-path identification; `cycles` remains the
better choice when the operator also wants frequency-aware accounting
or microarchitectural pairing (cache misses, branch mispredict rates),
i.e. on hosts where paranoid is already lowered or `CAP_SYS_ADMIN` is
granted to the unit.

Things that are independent of `paranoid` and work everywhere:

- **All log-derived panels** (URLs, Plugins, Caches, Services, Active,
  Keys, Overview, Logs). They only need read access to the access logs.
- **The Proc panel's memory / IO / threads / fds / page-fault counters**.
  All from `/proc/PID/{stat,smaps,io,status}` — paranoid doesn't gate
  `/proc` reads.
- **biolat / runqlat** if `bcc-tools` is installed and `kheaders` is
  loaded: these read kernel ring buffers via eBPF, controlled by the
  bcc-tools setup, not by `perf_event_paranoid`.

The two big "✗ at default" entries are **off-CPU** and the **flame-graph
modes** (page-fault, wakeup, block-I/O, malloc) — exactly the ones an
operator reaches for when the on-CPU profile says "the bottleneck isn't
CPU." If those matter to you, lowering paranoid is the right call.

## Changing the value manually

When `smartmet-webmon` is installed, the package handles this for
you. The notes below are for hosts that don't run the package (e.g.
hosts that only have `smartmet-monitor` for `smtop` / `bstat`) but
where an operator still wants to lower paranoid for ad-hoc work.

### Read the current setting

```sh
sysctl kernel.perf_event_paranoid
# or, equivalently
cat /proc/sys/kernel/perf_event_paranoid
```

### Change it transiently (until reboot)

```sh
sudo sysctl kernel.perf_event_paranoid=0
```

Useful for "is this what's blocking me?" experiments. Reverts to
distribution default at next boot.

### Change it persistently

Drop a file under `/etc/sysctl.d/`. The naming convention is
`NN-name.conf` where NN is a two-digit ordering prefix; `99-` runs late
so site-local overrides win.

```sh
sudo install -m 0644 /dev/stdin /etc/sysctl.d/99-perf-paranoid.conf <<'EOF'
kernel.perf_event_paranoid = 0
EOF

sudo sysctl --system            # apply now without reboot
```

### Verify

```sh
sysctl kernel.perf_event_paranoid

# Run perf as some unprivileged user — this should now exit 0
sudo -u nobody perf record -F 99 --call-graph=dwarf,32768 \
    -p $(pgrep $$) \
    -o /tmp/perf-test.data -- sleep 1
echo "exit=$?"
```

### Override the smartmet-webmon defaults

If you need a different value than the package ships:

```sh
# Override smartmet-webmon's 99-smartmet-perf.conf with a higher-
# numbered file in /etc/ (which beats /usr/lib/) — RPM upgrades
# won't touch your override.
echo 'kernel.perf_event_paranoid = 2' | \
    sudo tee /etc/sysctl.d/99-site-paranoid.conf
sudo sysctl --system
```

## The bcc-tools `kheaders` gotcha

`offcputime-bpfcc`, `biolatency-bpfcc`, `runqlat-bpfcc` need the kernel
header symbols at runtime. Modern kernels expose these through a
`kheaders` module. Loading a module is restricted to root, so a
non-root daemon (smwebmon as `smartmet-server`) can't `modprobe
kheaders` on demand.

The `smartmet-webmon` package handles this: it ships
`/usr/lib/modules-load.d/smartmet-perf.conf` (one line: `kheaders`)
and runs `modprobe kheaders` from `%post` so the module is available
in the running kernel without a reboot. Subsequent boots load it via
`systemd-modules-load`.

For hosts without `smartmet-webmon` installed (CLI-only deployments
that still want the off-CPU flame in `smtop`):

```sh
echo "kheaders" | sudo tee /etc/modules-load.d/kheaders.conf
sudo modprobe kheaders                # take effect now without reboot
```

This is independent of `paranoid` — even with `paranoid = -1`, the
modprobe step would fail without it.

## Alternative: grant the unit perf capabilities (no system-wide change)

If you don't want to lower `paranoid` system-wide — for example on a
multi-tenant host where other users share the kernel — grant the
smwebmon unit the equivalent capability via a systemd drop-in instead.

On RHEL 8 (kernel 4.18, no `CAP_PERFMON`), use `CAP_SYS_ADMIN`:

```sh
sudo systemctl edit smartmet-webmon
```

```ini
[Service]
NoNewPrivileges=no
AmbientCapabilities=CAP_SYS_ADMIN
CapabilityBoundingSet=CAP_SYS_ADMIN
```

On RHEL 9 / Fedora / kernel 5.8+, prefer the narrower `CAP_PERFMON`:

```ini
[Service]
NoNewPrivileges=no
AmbientCapabilities=CAP_PERFMON
CapabilityBoundingSet=CAP_PERFMON
```

Trade-offs: this is more surgical (only smwebmon gets the capability,
the rest of the system is untouched), but `CAP_SYS_ADMIN` is a broad
hammer — it grants a lot more than just perf. `CAP_PERFMON` is the
correct fit when available.

You also need to drop `NoNewPrivileges=yes` for ambient capabilities to
take effect. The hardened defaults the unit ships with are otherwise
preserved.

## Security implications of lowering `paranoid`

The kernel docs are explicit:

> Lower values increase the access control surface for unprivileged
> users. The default value of 2 is the most restrictive.

What changes practically when you go from `2` to `0`:

- Any user on the host can read CPU performance counters for their own
  processes. They cannot read events for other users' processes
  (paranoid = -1 would allow that).
- Any user can profile **kernel** code while it's executing on behalf
  of their own process. Kernel addresses become visible in the profile.
  This leaks information about kernel function locations — useful for
  exploits that need to bypass KASLR.
- Tracepoint-event sampling (sched_switch, page-faults, etc.) becomes
  available, which can be combined into a fairly detailed picture of
  what other processes on the system are doing.

For a SmartMet backend that runs only the smartmet-server user's
processes plus operators logging in via SSH for ops work, the practical
exposure increase is small. For a multi-tenant host, the calculus is
different.

## Choosing a value — by use case

**Operator workstation, used interactively for ops only.** Keep the RHEL
default `paranoid = 2`. Run `smtop --perf` via `sudo` when you need the
flame view; the privileged invocation bypasses paranoid. No daemon
running, no security trade-off needed at the OS level.

**Production SmartMet backend, runs `smartmet-webmon` as a daemon.**
`paranoid = 0` plus `kheaders` pre-loaded. Lets the dashboard work as
designed (all flame modes, perfstat, biolat, runqlat) without granting
the unit elevated capabilities. Restart the unit after the change to
pick up the new permissions.

**Hardened / multi-tenant / customer-shared host, can't lower paranoid.**
Grant the unit `CAP_SYS_ADMIN` (or `CAP_PERFMON` on kernel 5.8+) via a
drop-in. With the capability, every flame mode works regardless of the
system-wide paranoid value. Document the override in your
config-management system so it's not lost on the next reinstall.

## Where to read more

- Kernel `Documentation/admin-guide/perf-security.rst`:
  <https://www.kernel.org/doc/html/latest/admin-guide/perf-security.html>
- `proc(5)` — section on `/proc/sys/kernel/perf_event_*`.
- `man 2 perf_event_open` — the syscall whose access this sysctl gates.
- `bcc-tools` docs: <https://github.com/iovisor/bcc> for the `kheaders`
  / privileges story.
