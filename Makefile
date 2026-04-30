PREFIX ?= /usr
DESTDIR ?=

# On RHEL 8 `/usr/bin/python3` is 3.6 (platform-python); the packaged
# module we actually target is `/usr/bin/python3.9`. Override PYTHON=
# at invocation (the RPM spec does this on RHEL 8) to point at a
# specific interpreter. Everywhere else `python3` already is ≥ 3.9.
PYTHON ?= python3

# Resolve the Python 3 site-packages dir for the chosen interpreter
# (e.g. /usr/lib/python3.9/site-packages). Override PYSITELIB= to force
# a specific path (the RPM does this).
PYSITELIB ?= $(shell $(PYTHON) -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')

BINDIR = $(DESTDIR)$(PREFIX)/bin
SHAREDIR = $(DESTDIR)$(PREFIX)/share/smartmet
MANDIR = $(DESTDIR)$(PREFIX)/share/man/man1
DOCDIR = $(DESTDIR)$(PREFIX)/share/doc/smartmet-monitor
SITEDIR = $(DESTDIR)$(PYSITELIB)/smartmet_top

# smartmet-webmon — browser-dashboard companion. Ships as a separate
# RPM via the install-webmon target so smartmet-monitor stays free of
# the systemd / asset / extra-package weight.
SITEDIR_WEBMON = $(DESTDIR)$(PYSITELIB)/smartmet_webmon
WEBMON_ASSET_DIR = $(SHAREDIR)/webmon
UNITDIR ?= /usr/lib/systemd/system
SYSCONFDIR ?= /etc

.PHONY: all install uninstall clean check rpm install-webmon \
        uninstall-webmon webmon-rpm rpms _stage-tarball

all:
	@echo "smartmet-monitor is a no-build package. Use 'make install' or 'make rpm'."

BTOOLS = bstat bchart burls bstatus bkeys bperf
LEGACY = bstat1s bstat10s bstat1 bstat10 bstat60 bstat24
MANPAGES = smtop.1 bstat.1 bchart.1 burls.1 bstatus.1 bkeys.1 bperf.1 \
           bstat1s.1 bstat10s.1 bstat1.1 bstat10.1 bstat60.1 bstat24.1
WEBMON_ASSETS = index.html app.js chart.js style.css

install:
	install -d $(BINDIR) $(SHAREDIR) $(MANDIR) $(DOCDIR)
	install -d $(SITEDIR) $(SITEDIR)/panels $(SITEDIR)/sources $(SITEDIR)/state $(SITEDIR)/views $(SITEDIR)/widgets
	# smtop plus bstat-family command wrappers
	install -m 0755 smtop $(BINDIR)/smtop
	$(foreach t,$(BTOOLS),install -m 0755 bin/$(t) $(BINDIR)/$(t); )
	# legacy compatibility aliases: bstat1s/10s/1/10/60/24
	$(foreach t,$(LEGACY),install -m 0755 bin/$(t) $(BINDIR)/$(t); )
	# shared library that all bstat-family wrappers source
	install -m 0644 share/smartmet/bstat.sh $(SHAREDIR)/bstat.sh
	# bperf is a Python script (heavier than awk warrants); the wrapper
	# in $(BINDIR)/bperf execs it with python3.9.
	install -m 0644 share/smartmet/bperf.py $(SHAREDIR)/bperf.py
	# python package
	install -m 0644 smartmet_top/*.py         $(SITEDIR)/
	install -m 0644 smartmet_top/panels/*.py  $(SITEDIR)/panels/
	install -m 0644 smartmet_top/sources/*.py $(SITEDIR)/sources/
	install -m 0644 smartmet_top/state/*.py   $(SITEDIR)/state/
	install -m 0644 smartmet_top/views/*.py   $(SITEDIR)/views/
	install -m 0644 smartmet_top/widgets/*.py $(SITEDIR)/widgets/
	# man pages
	$(foreach m,$(MANPAGES),install -m 0644 doc/man/$(m) $(MANDIR)/$(m); )
	# README + the screenshot images it references
	install -m 0644 README.md $(DOCDIR)/README.md
	install -d $(DOCDIR)/images
	install -m 0644 doc/images/*.png $(DOCDIR)/images/
	# symlink for discoverability alongside smartmet-library-* and friends
	ln -sf smtop $(BINDIR)/smartmet-top

uninstall:
	rm -f $(BINDIR)/smtop $(BINDIR)/smartmet-top
	$(foreach t,$(BTOOLS),rm -f $(BINDIR)/$(t); )
	$(foreach t,$(LEGACY),rm -f $(BINDIR)/$(t); )
	rm -f $(SHAREDIR)/bstat.sh
	rm -f $(SHAREDIR)/bperf.py
	$(foreach m,$(MANPAGES),rm -f $(MANDIR)/$(m); )
	rm -rf $(DOCDIR)
	rm -rf $(SITEDIR)

install-webmon:
	install -d $(BINDIR) $(SITEDIR_WEBMON) $(WEBMON_ASSET_DIR) $(MANDIR)
	install -d $(DESTDIR)$(UNITDIR) $(DESTDIR)$(SYSCONFDIR)/sysconfig
	install -m 0755 smwebmon $(BINDIR)/smwebmon
	install -m 0644 smartmet_webmon/*.py $(SITEDIR_WEBMON)/
	$(foreach a,$(WEBMON_ASSETS), \
	    install -m 0644 share/smartmet/webmon/$(a) $(WEBMON_ASSET_DIR)/$(a); )
	install -m 0644 share/systemd/smartmet-webmon.service \
	    $(DESTDIR)$(UNITDIR)/smartmet-webmon.service
	install -m 0644 share/sysconfig/smartmet-webmon \
	    $(DESTDIR)$(SYSCONFDIR)/sysconfig/smartmet-webmon
	install -m 0644 doc/man/smwebmon.1 $(MANDIR)/smwebmon.1

uninstall-webmon:
	rm -f $(BINDIR)/smwebmon
	rm -rf $(SITEDIR_WEBMON) $(WEBMON_ASSET_DIR)
	rm -f $(DESTDIR)$(UNITDIR)/smartmet-webmon.service
	rm -f $(DESTDIR)$(SYSCONFDIR)/sysconfig/smartmet-webmon
	rm -f $(MANDIR)/smwebmon.1

check:
	$(PYTHON) -c 'import sys; sys.path.insert(0, "."); \
	    import smartmet_top, smartmet_top.app, \
	           smartmet_top.sources.proc, smartmet_top.sources.perftop, \
	           smartmet_top.panels.proc, smartmet_top.panels.flame, \
	           smartmet_top.widgets.bars, \
	           smartmet_top.views.live, smartmet_top.views.admin, \
	           smartmet_top.views.composite; \
	    from smartmet_top.widgets.bars import _braille_cell; \
	    btop = [" ","⢀","⢠","⢰","⢸","⡀","⣀","⣠","⣰","⣸", \
	            "⡄","⣄","⣤","⣴","⣼","⡆","⣆","⣦","⣶","⣾", \
	            "⡇","⣇","⣧","⣷","⣿"]; \
	    [_braille_cell(l,r)==btop[l*5+r] or (_ for _ in ()).throw(SystemExit(f"Braille (l={l},r={r}): {_braille_cell(l,r)!r}!={btop[l*5+r]!r}")) for l in range(5) for r in range(5)]; \
	    from smartmet_top.sources.logparse import parse_iso; \
	    assert parse_iso("2026-04-25T19:57:49,567645") == parse_iso("2026-04-25T19:57:49.567645"), "parse_iso must accept SmartMet`s comma decimal separator"; \
	    from smartmet_top.widgets.bars import sparkline, vchart, set_ascii; \
	    set_ascii(False); assert sparkline([0,1,2,3,4,5,6,7,8], width=4); \
	    set_ascii(True);  assert sparkline([0,1,2,3,4,5,6,7,8], width=4); \
	    set_ascii(False); assert len(vchart([0,1,2,3,4,5,6,7,8], 3)) == 3; \
	    from smartmet_top.sources.perftop import parse_perf_script; \
	    assert parse_perf_script("smartmetd 1 [0] 1.0:    99 cycles:\n    deadbeef foo+0x0 (lib.so)\n\n")[0] == ("foo",); \
	    from smartmet_top.sources.offcpu import parse_offcputime_folded; \
	    folded = parse_offcputime_folded("smartmetd;__libc_start_main;main;futex_wait 1234567\nsmartmetd;io_schedule 555\nbroken line\n"); \
	    assert folded == [(("smartmetd","__libc_start_main","main","futex_wait"), 1234567), (("smartmetd","io_schedule"), 555)], folded; \
	    import smartmet_top.sources.offcpu, smartmet_top.sources.profile_caps; \
	    from smartmet_top.panels.proc import _build_flame_tree; \
	    tree = _build_flame_tree([(("a","b","c"), 100), (("a","b","d"), 50), ("a","b","e")]); \
	    assert tree["a"][0] == 151 and tree["a"][1]["b"][0] == 151 and tree["a"][1]["b"][1]["c"][0] == 100 and tree["a"][1]["b"][1]["d"][0] == 50 and tree["a"][1]["b"][1]["e"][0] == 1, tree; \
	    import smartmet_top.sources.biolat; \
	    from smartmet_top.sources.biolat import parse_biolatency, percentiles_us; \
	    sample_text = "Tracing block device I/O...\n\n     usecs               : count     distribution\n         0 -> 1          : 0\n         2 -> 3          : 5\n         4 -> 7          : 27\n         8 -> 15         : 173\n        16 -> 31         : 50\n"; \
	    bks, unit = parse_biolatency(sample_text); \
	    assert unit == "usecs" and bks == [(0,1,0),(2,3,5),(4,7,27),(8,15,173),(16,31,50)], (bks, unit); \
	    p50, p95, p99, tot = percentiles_us(bks, unit); \
	    assert tot == 255 and p50 == 15 and p95 == 31 and p99 == 31, (p50, p95, p99, tot); \
	    bks_ms, unit_ms = parse_biolatency("     msecs               : count     distribution\n         0 -> 1          : 100\n"); \
	    p50ms, _, _, totms = percentiles_us(bks_ms, unit_ms); \
	    assert unit_ms == "msecs" and totms == 100 and p50ms == 1000, (p50ms, totms, unit_ms); \
	    from smartmet_top.state.store import ProcSample; \
	    from smartmet_top.panels.proc import _majflt_rate, _majflt_rate_series; \
	    s = [ProcSample(ts=10.0, majflt=100), ProcSample(ts=11.0, majflt=200), ProcSample(ts=12.0, majflt=210)]; \
	    assert _majflt_rate(s) == 10.0, _majflt_rate(s); \
	    assert _majflt_rate_series(s) == [100.0, 10.0], _majflt_rate_series(s); \
	    assert _majflt_rate([]) == 0.0 and _majflt_rate([s[0]]) == 0.0; \
	    import smartmet_top.sources.netstats; \
	    from smartmet_top.sources.netstats import _parse_proc_net_snmp, parse_proc_net_dev; \
	    snmp = _parse_proc_net_snmp("Tcp: RtoAlgorithm RtoMin RtoMax MaxConn ActiveOpens PassiveOpens AttemptFails EstabResets CurrEstab InSegs OutSegs RetransSegs InErrs OutRsts InCsumErrors\nTcp: 1 200 120000 -1 11 22 0 0 1 100 200 17 0 5 0\n"); \
	    assert snmp["Tcp"]["RetransSegs"] == 17 and snmp["Tcp"]["InSegs"] == 100, snmp; \
	    dev = parse_proc_net_dev("Inter-|   Receive                                                |  Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n    lo: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n  eth0: 5000 50 0 0 0 0 0 0 6000 60 0 0 0 0 0 0\n"); \
	    assert "lo" not in dev and dev["eth0"] == (5000, 6000), dev; \
	    import smartmet_top.sources.perfstat, smartmet_top.sources.runqlat; \
	    from smartmet_top.sources.perfstat import parse_perf_stat_x, derive_ratios; \
	    cx = parse_perf_stat_x("100,,cycles\n50,,instructions\n200,,cache-references\n20,,cache-misses\n5,,branch-misses\n"); \
	    assert cx == {"cycles":100,"instructions":50,"cache-references":200,"cache-misses":20,"branch-misses":5}, cx; \
	    ipc, cm, bm = derive_ratios(cx); \
	    assert abs(ipc - 0.5) < 1e-9 and abs(cm - 0.1) < 1e-9 and abs(bm - 0.1) < 1e-9, (ipc, cm, bm); \
	    cx2 = parse_perf_stat_x("<not supported>,,cycles\n50,,instructions\n"); \
	    assert cx2 == {"cycles":0,"instructions":50}, cx2; \
	    ipc2, _, _ = derive_ratios(cx2); \
	    assert ipc2 == 0.0; \
	    from smartmet_top.state.alerts import Alert; \
	    from smartmet_top.state.store import Store; \
	    from smartmet_top.panels.alerts_overlay import handle_alerts_key, _format_age; \
	    st = Store(); \
	    st.upsert_alert(Alert(id="t1", severity="warn", detector="t", title="A", detail="d", suggested_panel="p")); \
	    st.upsert_alert(Alert(id="t2", severity="crit", detector="t", title="B", detail="d", suggested_panel="f")); \
	    summary = st.alerts_summary(); \
	    assert summary == (2, "crit"), summary; \
	    active = st.alerts_active(); \
	    assert [a.id for a in active] == ["t2", "t1"], [a.id for a in active]; \
	    unviewed = st.alerts_unviewed(); \
	    assert len(unviewed) == 2; \
	    st.mark_alerts_viewed(); \
	    assert st.alerts_unviewed() == []; \
	    assert len(st.alerts_active()) == 2; \
	    st.alert_dismiss("t1"); \
	    assert [a.id for a in st.alerts_active()] == ["t2"]; \
	    assert st.alerts_for("p") == []; \
	    assert [a.id for a in st.alerts_for("f")] == ["t2"]; \
	    assert _format_age(45) == "45s" and _format_age(120) == "2m" and _format_age(3700) == "1h"; \
	    from smartmet_top.sources import profile_caps as _pc; \
	    assert "/usr/share/bcc/tools" in _pc._BCC_SEARCH_DIRS, _pc._BCC_SEARCH_DIRS; \
	    assert _pc._find_bcc_tool("definitely-not-installed-bpfcc") is None; \
	    assert "bcc-tools" in _pc._bcc_install_hint("offcputime"); \
	    assert "/usr/share/bcc/tools" in _pc._bcc_install_hint("offcputime"); \
	    import smartmet_top.sources.vmstats; \
	    from smartmet_top.sources.vmstats import _coalesce; \
	    d = {"pgsteal_kswapd_normal": 100, "pgsteal_kswapd_dma32": 50, "pgsteal_direct": 7}; \
	    assert _coalesce(d, "pgsteal_kswapd", "pgsteal_kswapd_normal") == 100; \
	    assert _coalesce(d, "pgsteal_direct", "pgsteal_direct_normal") == 7; \
	    assert _coalesce(d, "missing_key") == 0; \
	    from smartmet_top.panels.proc import ProcPanel; \
	    from smartmet_top.state.store import Store; \
	    _st = Store(); \
	    [_st.netstats_record_iface(0.0+i, "eth0", 5_000_000, 1_000_000) for i in range(15)]; \
	    [_st.netstats_record_iface(0.0+i, "eth1", 100_000, 9_000_000) for i in range(15)]; \
	    [_st.netstats_record_iface(0.0+i, "lo", 50_000, 50_000) for i in range(15)]; \
	    picks = ProcPanel._pick_busiest_ifaces(_st, ["eth0","eth1","lo"]); \
	    assert picks == [("rx-busy","eth0"),("tx-busy","eth1")], picks; \
	    _st2 = Store(); \
	    [_st2.netstats_record_iface(0.0+i, "eth0", 5_000_000, 9_000_000) for i in range(15)]; \
	    [_st2.netstats_record_iface(0.0+i, "lo", 50_000, 50_000) for i in range(15)]; \
	    picks2 = ProcPanel._pick_busiest_ifaces(_st2, ["eth0","lo"]); \
	    assert picks2 == [("busiest","eth0")], picks2; \
	    import smartmet_top.sources.pagefault; \
	    from smartmet_top.panels.flame import _is_lock_stack, _MODES; \
	    assert "on-cpu" in _MODES and "wakeup" in _MODES and "blockflame" in _MODES and "malloc" in _MODES; \
	    import smartmet_top.sources.wakeup, smartmet_top.sources.blockflame; \
	    import smartmet_top.sources.mallocflame; \
	    from smartmet_top.sources.mallocflame import parse_bpftrace_stacks; \
	    bp = "Attaching 2 probes...\n\n@[\n        malloc+0x0\n        foo+0x10\n        main+0x100\n]: 4096\n@[\n        malloc+0x0\n        bar\n]: 1024\n"; \
	    pa = parse_bpftrace_stacks(bp); \
	    assert pa == [(("main","foo","malloc"), 4096), (("bar","malloc"), 1024)], pa; \
	    from smartmet_top.sources.netstats import parse_proc_net_tcp; \
	    proc_tcp = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n   0: 0100007F:1F40 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0\n   1: 0100007F:0050 0202020A:1234 01 00000000:00000000 00:00000000 00000000     0        0\n   2: 0100007F:0050 0202020A:1235 06 00000000:00000000 00:00000000 00000000     0        0\n"; \
	    counts, listen = parse_proc_net_tcp(proc_tcp); \
	    assert counts["LISTEN"] == 1 and counts["ESTABLISHED"] == 1 and counts["TIME_WAIT"] == 1, counts; \
	    assert listen == [(8000, 0)], listen; \
	    import smartmet_top.panels.network; \
	    from smartmet_top.sources.smartmet_filter import collapse_to_smartmet, is_request_stack, keep_for_thread_class, THREAD_CLASS_REQUEST, THREAD_CLASS_BACKGROUND, THREAD_CLASS_ALL; \
	    assert collapse_to_smartmet(("__libc_start_main","main","SmartMet::Spine::Reactor::run","SmartMet::Spine::SmartMetPlugin::callRequestHandler","pthread_mutex_lock","futex_wait","do_syscall_64")) == ("SmartMet::Spine::Reactor::run","SmartMet::Spine::SmartMetPlugin::callRequestHandler","pthread_mutex_lock"); \
	    assert collapse_to_smartmet(("main","libc_only","futex_wait")) is None; \
	    assert is_request_stack(("SmartMet::Spine::SmartMetPlugin::callRequestHandler","x")); \
	    assert not is_request_stack(("SmartMet::Engine::Cache::cleanup",)); \
	    assert keep_for_thread_class(("SmartMet::Spine::SmartMetPlugin::callRequestHandler","x"), THREAD_CLASS_REQUEST); \
	    assert keep_for_thread_class(("SmartMet::Engine::Cache::cleanup",), THREAD_CLASS_BACKGROUND); \
	    assert keep_for_thread_class((), THREAD_CLASS_ALL); \
	    from smartmet_top.panels.flame import _apply_filters; \
	    _af = _apply_filters([("main","SmartMet::A","pthread_mutex_lock","futex_wait"), ("main","libc_only","free")], thread_class=THREAD_CLASS_ALL, smartmet_only=True); \
	    assert _af == [("SmartMet::A","pthread_mutex_lock")], _af; \
	    _afw = _apply_filters([(("main","SmartMet::B","free"), 4096)], thread_class=THREAD_CLASS_ALL, smartmet_only=True); \
	    assert _afw == [(("SmartMet::B","free"), 4096)], _afw; \
	    import importlib.util as _ilu; \
	    _bp_spec = _ilu.spec_from_file_location("bperf_test", "share/smartmet/bperf.py"); \
	    _bp = _ilu.module_from_spec(_bp_spec); _bp_spec.loader.exec_module(_bp); \
	    _bp_text = "smartmetd 1 [0] 1.0:    99 cycles:\n    deadbeef pthread_mutex_lock+0x0 (libc.so)\n    deadbeef SmartMet::X::handle+0x0 (libsm.so)\n    deadbeef main+0x0 (smartmetd)\n\n"; \
	    _bp_st = _bp.parse_perf_script(_bp_text); \
	    assert _bp_st == [("main","SmartMet::X::handle","pthread_mutex_lock")], _bp_st; \
	    _bp_flt = _bp.filter_stacks(_bp_st, "all", smartmet_only=True); \
	    assert _bp_flt == [("SmartMet::X::handle","pthread_mutex_lock")], _bp_flt; \
	    import tempfile as _tf, os as _os; \
	    _tmpd = _tf.mkdtemp(prefix="bperf-test-"); \
	    _bp.write_folded(_bp.fold_stacks(_bp_flt), _os.path.join(_tmpd, "f.txt")); \
	    _bp.write_dot(_bp.fold_stacks(_bp_flt), _os.path.join(_tmpd, "g.dot"), "test"); \
	    _bp.write_svg(_bp.fold_stacks(_bp_flt), _os.path.join(_tmpd, "f.svg"), "test"); \
	    assert _os.path.getsize(_os.path.join(_tmpd, "f.svg")) > 200; \
	    assert "digraph bperf" in open(_os.path.join(_tmpd, "g.dot")).read(); \
	    import shutil as _sh; _sh.rmtree(_tmpd, ignore_errors=True); \
	    assert _is_lock_stack(("smartmetd","main","pthread_mutex_lock")); \
	    assert _is_lock_stack(("smartmetd","worker","__lll_lock_wait")); \
	    assert _is_lock_stack(("smartmetd","poll","futex_wait_queue_me")); \
	    assert not _is_lock_stack(("smartmetd","main","io_schedule")); \
	    assert not _is_lock_stack(()); \
	    from smartmet_top.state.store import Store as _S2; \
	    _stp = _S2(); \
	    _stp.proc_register(1234, cmdline="smartmetd-frontend", role="frontend"); \
	    _stp.proc_register(1100, cmdline="smartmetd-backend", role="backend"); \
	    _stp.proc_register(2200, cmdline="smartmetd-backend2", role="backend"); \
	    _stp.proc_register(900, cmdline="smartmetd-other", role="unknown"); \
	    assert _stp.proc_default_pid() == 1100, _stp.proc_default_pid(); \
	    _stp2 = _S2(); \
	    _stp2.proc_register(900, cmdline="x", role="unknown"); \
	    _stp2.proc_register(1234, cmdline="y", role="frontend"); \
	    assert _stp2.proc_default_pid() == 900, _stp2.proc_default_pid(); \
	    assert _S2().proc_default_pid() is None; \
	    import smartmet_top.runtime; \
	    from smartmet_top.snapshots.urls import URLsSnapshot; \
	    from smartmet_top.snapshots.services import ServicesSnapshot; \
	    from smartmet_top.snapshots.caches import CachesSnapshot; \
	    from smartmet_top.snapshots.active import ActiveSnapshot; \
	    from smartmet_top.snapshots.proc import ProcSnapshot; \
	    from smartmet_top.snapshots.overview import OverviewSnapshot; \
	    from smartmet_top.snapshots.network import NetworkSnapshot; \
	    from smartmet_top.snapshots.flame import FlameSnapshot; \
	    from smartmet_top.snapshots.keys import KeysSnapshot; \
	    from smartmet_top.snapshots.plugins import PluginsSnapshot; \
	    _wst = Store(); \
	    [(lambda h, r: (isinstance(h, list) and isinstance(r, list)) or (_ for _ in ()).throw(SystemExit(f"snapshot {_snap.name} returned non-list")))(*_snap.table(_wst)) for _snap in (URLsSnapshot, ServicesSnapshot, CachesSnapshot, ActiveSnapshot, ProcSnapshot, OverviewSnapshot, NetworkSnapshot, FlameSnapshot, KeysSnapshot, PluginsSnapshot)]; \
	    import smartmet_webmon, smartmet_webmon.assets, smartmet_webmon.handlers, smartmet_webmon.server; \
	    from smartmet_top.snapshots.logs import LogsSnapshot as _Ls; \
	    assert _Ls.table(_wst) == (["line"], []); \
	    [(lambda r, fn: (lambda res: (res[0] in (200, 400) and isinstance(res[1], dict)) or (_ for _ in ()).throw(SystemExit(f"handler {r}: {res}")))(fn(_wst, {})))(_route, _fn) for _route, _fn in smartmet_webmon.handlers.ROUTES.items()]; \
	    _wserv = smartmet_webmon.server.WebServer(_wst, bind=("127.0.0.1", 0), asset_root="."); \
	    _wserv.start(); \
	    import urllib.request as _ur, json as _json; \
	    _resp = _ur.urlopen(f"http://127.0.0.1:{_wserv.port}/api/health", timeout=2).read(); \
	    assert _json.loads(_resp).get("ok"); \
	    _resp = _ur.urlopen(f"http://127.0.0.1:{_wserv.port}/api/panels", timeout=2).read(); \
	    _panels = _json.loads(_resp).get("panels"); \
	    assert isinstance(_panels, list) and len(_panels) >= 10, _panels; \
	    _wserv.stop()'
	$(PYTHON) -m py_compile smartmet_top/*.py smartmet_top/*/*.py
	$(PYTHON) -m py_compile smartmet_webmon/*.py
	$(PYTHON) -m py_compile share/smartmet/bperf.py
	bash -n share/smartmet/bstat.sh
	for t in $(BTOOLS) $(LEGACY); do bash -n bin/$$t; done
	# End-to-end: each wrapper must load the library and print at least
	# its --help section without crashing.
	for t in $(BTOOLS) $(LEGACY); do \
	    SMARTMET_MONITOR_LIB=$(CURDIR)/share/smartmet/bstat.sh \
	        bin/$$t --help >/dev/null || exit 1; \
	done
	# smtop -h must work (catches argparse / import-time wiring bugs).
	$(PYTHON) -m smartmet_top --help >/dev/null
	# smwebmon -h must work too.
	$(PYTHON) -m smartmet_webmon --help >/dev/null

clean:
	find . -name __pycache__ -prune -exec rm -rf {} +

# Build RPM(s) from HEAD. Uses ~/.rpmmacros for %_topdir, matching
# other smartmet-* repos in this hub (e.g. macgyver, spine).
#
# Both spec files share the same Source0: tarball, so `rpmbuild -tb`
# refuses (it requires exactly one spec inside the tarball). Instead
# we stage the tarball in rpm's %_sourcedir once per `make` invocation
# and then call `rpmbuild -bb <spec>` per spec — the same flow common
# in multi-subpackage builds elsewhere in the SmartMet ecosystem.
NAME = smartmet-monitor
VERSION = $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' smartmet_top/__init__.py)
TARBALL = $(NAME)-$(VERSION).tar.gz

# Internal: build the source tarball and copy it into %_sourcedir.
# Phony so it always runs, but make still deduplicates the call when
# multiple downstream targets list it (so `make rpms` archives HEAD
# exactly once, not three times).
_stage-tarball:
	rm -f $(TARBALL)
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ HEAD \
	    -o $(TARBALL)
	SOURCEDIR=`rpm --eval '%_sourcedir'`; \
	    install -d "$$SOURCEDIR"; \
	    cp $(TARBALL) "$$SOURCEDIR/"

rpm: clean $(NAME).spec _stage-tarball
	rpmbuild -bb $(NAME).spec
	rm -f $(TARBALL)

webmon-rpm: clean smartmet-webmon.spec _stage-tarball
	rpmbuild -bb smartmet-webmon.spec
	rm -f $(TARBALL)

# Build both RPMs in a single make invocation. Shared dependencies
# (clean, _stage-tarball) run once thanks to make's DAG; the local
# TARBALL may be removed by the first sub-target before the second
# runs, but the %_sourcedir copy (placed by _stage-tarball) persists,
# so rpmbuild -bb still finds it.
rpms: rpm webmon-rpm
