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
SYSCTLDIR ?= /usr/lib/sysctl.d
MODLOADDIR ?= /usr/lib/modules-load.d

.PHONY: all install uninstall clean check rpm install-webmon \
        uninstall-webmon webmon-rpm rpms _stage-tarball

all:
	@echo "smartmet-monitor is a no-build package. Use 'make install' or 'make rpm'."

BTOOLS = bstat bchart burls bstatus bkeys bperf
LEGACY = bstat1s bstat10s bstat1 bstat10 bstat60 bstat24
MANPAGES = smtop.1 bstat.1 bchart.1 burls.1 bstatus.1 bkeys.1 bperf.1 \
           bstat1s.1 bstat10s.1 bstat1.1 bstat10.1 bstat60.1 bstat24.1

install:
	install -d $(BINDIR) $(SHAREDIR) $(MANDIR) $(DOCDIR) $(SITEDIR)
	install -d $(DESTDIR)$(SYSCTLDIR)
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
	# python package — top-level modules + every subpackage. The
	# subpackage list is auto-discovered (was an explicit list once,
	# but adding a new subpackage and forgetting to update the list
	# silently shipped a broken RPM — caught the hard way with the
	# snapshots/ directory in 26.4.30-2).
	install -m 0644 smartmet_top/*.py $(SITEDIR)/
	for d in smartmet_top/*/; do \
	    sub=`basename "$$d"`; \
	    case "$$sub" in __pycache__) continue;; esac; \
	    install -d "$(SITEDIR)/$$sub"; \
	    install -m 0644 $$d*.py "$(SITEDIR)/$$sub/"; \
	done
	# man pages
	$(foreach m,$(MANPAGES),install -m 0644 doc/man/$(m) $(MANDIR)/$(m); )
	# README + the screenshot images it references + reference docs
	install -m 0644 README.md $(DOCDIR)/README.md
	install -m 0644 doc/perf-event-paranoid.md $(DOCDIR)/perf-event-paranoid.md
	install -d $(DOCDIR)/images
	install -m 0644 doc/images/*.png $(DOCDIR)/images/
	# Vendor sysctl drop-in. Every setting is commented out — the file
	# documents what the Flame panel needs without modifying host
	# policy on install. Site-managed /etc/sysctl.d/99-smartmet-perf.conf
	# wins via systemd-sysctl precedence.
	install -m 0644 share/sysctl.d/99-smartmet-perf.conf \
	    $(DESTDIR)$(SYSCTLDIR)/99-smartmet-perf.conf
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
	rm -f $(DESTDIR)$(SYSCTLDIR)/99-smartmet-perf.conf

install-webmon:
	install -d $(BINDIR) $(SITEDIR_WEBMON) $(WEBMON_ASSET_DIR) $(MANDIR)
	install -d $(DESTDIR)$(UNITDIR) $(DESTDIR)$(SYSCONFDIR)/sysconfig
	install -d $(DESTDIR)$(SYSCTLDIR) $(DESTDIR)$(MODLOADDIR)
	install -d $(DESTDIR)$(SYSCONFDIR)/smartmet-webmon
	install -m 0755 smwebmon $(BINDIR)/smwebmon
	install -m 0644 smartmet_webmon/*.py $(SITEDIR_WEBMON)/
	# Cluster definitions — empty by default (single-host mode).
	# Operators uncomment the relevant cluster section for their
	# deployment. %config(noreplace) in the spec so site edits
	# survive upgrades.
	install -m 0644 share/smartmet-webmon/clusters.conf \
	    $(DESTDIR)$(SYSCONFDIR)/smartmet-webmon/clusters.conf
	# Browser assets — auto-discovered so adding a new file (e.g.
	# flame.js was missed in 26.4.30-3 because it wasn't in a
	# hand-maintained WEBMON_ASSETS list) doesn't silently produce
	# a broken RPM. Skips dirs so a future vendor/ subtree can be
	# handled separately.
	for f in share/smartmet/webmon/*; do \
	    [ -f "$$f" ] || continue; \
	    install -m 0644 "$$f" $(WEBMON_ASSET_DIR)/; \
	done
	install -m 0644 share/systemd/smartmet-webmon.service \
	    $(DESTDIR)$(UNITDIR)/smartmet-webmon.service
	install -m 0644 share/sysconfig/smartmet-webmon \
	    $(DESTDIR)$(SYSCONFDIR)/sysconfig/smartmet-webmon
	# kheaders pre-load so bcc-tools (offcputime-bpfcc, biolat-bpfcc,
	# runqlat-bpfcc) can run as a non-root daemon. The perf
	# paranoid sysctl is shipped (commented out) by smartmet-monitor
	# at /usr/lib/sysctl.d/99-smartmet-perf.conf — webmon does not
	# touch host security policy on install.
	install -m 0644 share/modules-load.d/smartmet-perf.conf \
	    $(DESTDIR)$(MODLOADDIR)/smartmet-perf.conf
	install -m 0644 doc/man/smwebmon.1 $(MANDIR)/smwebmon.1
	# RIR delegated-stats files — IP→country lookup for the
	# Countries panel and the IP Flow rim labels. Bundled with
	# the RPM during the test phase; long-term replaced by an
	# explicit refresh mechanism (a daily timer + curl, or
	# operator-driven). Skip the install if the dir is absent so
	# a contributor without the snapshot can still build the RPM.
	if [ -d share/smartmet/country-db ]; then \
	    install -d $(SHAREDIR)/country-db; \
	    for f in share/smartmet/country-db/delegated-*-extended-latest; do \
	        [ -f "$$f" ] || continue; \
	        install -m 0644 "$$f" $(SHAREDIR)/country-db/; \
	    done; \
	fi

uninstall-webmon:
	rm -f $(BINDIR)/smwebmon
	rm -rf $(SITEDIR_WEBMON) $(WEBMON_ASSET_DIR)
	rm -rf $(SHAREDIR)/country-db
	rm -f $(DESTDIR)$(UNITDIR)/smartmet-webmon.service
	rm -f $(DESTDIR)$(SYSCONFDIR)/sysconfig/smartmet-webmon
	rm -rf $(DESTDIR)$(SYSCONFDIR)/smartmet-webmon
	rm -f $(DESTDIR)$(MODLOADDIR)/smartmet-perf.conf
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
	    from smartmet_top.sources.mallocstats import parse_jemalloc_json, parse_mallocstats; \
	    import json as _json; \
	    _je_dict = {"jemalloc": {"version": "5.2.1-0-g1234", "stats": {"allocated": 1000, "active": 1500, "metadata": 250, "resident": 2000, "mapped": 3000, "retained": 500}, "stats.arenas": {"narenas": 4}}}; \
	    _je_doc = _json.dumps(_je_dict); \
	    _ms = parse_jemalloc_json(_je_doc); \
	    assert _ms is not None and _ms.allocator == "jemalloc", _ms; \
	    assert _ms.allocated == 1000 and _ms.active == 1500 and _ms.resident == 2000, (_ms.allocated, _ms.active, _ms.resident); \
	    assert _ms.narenas == 4 and _ms.version == "5.2.1-0-g1234", _ms; \
	    assert abs(_ms.fragmentation_pct - (500/1500*100)) < 1e-6, _ms.fragmentation_pct; \
	    assert parse_mallocstats(_je_doc) is not None; \
	    assert parse_mallocstats("") is None; \
	    assert parse_mallocstats("not json") is None; \
	    assert parse_mallocstats(_json.dumps({"jemalloc": {"version": "x"}})) is None, "stats key required"; \
	    from smartmet_top.snapshots.heap import HeapSnapshot; \
	    from smartmet_top.state.store import Store as _HeapStore; \
	    _hs = _HeapStore(); _hs.register_admin_host("c3.back"); \
	    _hs.mallocstats_latest["c3.back"] = _ms; \
	    _hs.mallocstats_history["c3.back"].append(_ms); \
	    _hd = HeapSnapshot.detail(_hs); \
	    assert len(_hd["hosts"]) == 1 and _hd["hosts"][0]["host"] == "c3.back"; \
	    assert _hd["hosts"][0]["latest"]["allocated"] == 1000; \
	    from smartmet_top.sources.smartmet_filter import is_smartmet_frame; \
	    assert is_smartmet_frame("SmartMet::Spine::Reactor::run"); \
	    assert is_smartmet_frame("Fmi::Cache::insert"); \
	    assert is_smartmet_frame("NFmiArea::xy"); \
	    assert is_smartmet_frame("NFmiPoint::operator+"); \
	    assert is_smartmet_frame("Giza::Box::draw"); \
	    assert is_smartmet_frame("Imagine::NFmiColorTools::reduce"); \
	    assert is_smartmet_frame("Locus::Query::execute"); \
	    assert is_smartmet_frame("Trax::contour"); \
	    assert is_smartmet_frame("GRIB2::Decoder::decode"); \
	    assert is_smartmet_frame("Observation::PostgreSQLDriver::query"); \
	    assert is_smartmet_frame("TextGen::Sentence::realize"); \
	    assert is_smartmet_frame("_ZN8SmartMet5Spine7Reactor3runEv"), "mangled fallback"; \
	    assert not is_smartmet_frame("memcpy"); \
	    assert not is_smartmet_frame("std::vector<int>::push_back"); \
	    assert not is_smartmet_frame("__schedule"); \
	    assert not is_smartmet_frame("FMI::DoSomething"), "FMI:: (capital) deliberately excluded — fmitools/qdtools only"; \
	    assert not is_smartmet_frame("DataTransform::convert"), "qdtools/fmitools only"; \
	    assert not is_smartmet_frame("HDF5::Reader::open"), "qdtools-only"; \
	    assert not is_smartmet_frame("NFmi"), "exact-match guard — must be NFmi[A-Z]"; \
	    assert not is_smartmet_frame("NFmiscellaneous"), "NFmi[a-z] should not match"; \
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
	    _np = _ur.build_opener(_ur.ProxyHandler({})); \
	    _resp = _np.open(f"http://127.0.0.1:{_wserv.port}/api/health", timeout=2).read(); \
	    assert _json.loads(_resp).get("ok"); \
	    _resp = _np.open(f"http://127.0.0.1:{_wserv.port}/api/panels", timeout=2).read(); \
	    _panels = _json.loads(_resp).get("panels"); \
	    assert isinstance(_panels, list) and len(_panels) >= 10, _panels; \
	    _wserv.stop(); \
	    from smartmet_webmon.clusters import parse_clusterinfo, _derive_cluster_domain, _cluster_name_from_prefixes; \
	    _ci_lines = ["    URI /", "        c2/", "    URI /c1/", "    URI /c1/timeseries", "    URI /c2/", "        c2/", "        c2/timeseries", "    URI /c2/timeseries", "        c2/timeseries", "    URI /v1.q3/", "        v1.q3/"]; \
	    _bs = parse_clusterinfo(chr(10).join(_ci_lines)); \
	    _by = {b.prefix: b for b in _bs}; \
	    assert set(_by) == {"c1", "c2", "v1.q3"}, list(_by); \
	    assert _by["c1"].alive is False and _by["c2"].alive and _by["v1.q3"].alive, _by; \
	    assert _derive_cluster_domain("c3.back.smartmet.fmi.fi") == "back.smartmet.fmi.fi"; \
	    assert _derive_cluster_domain("in1.back.smartmet.fmi.fi") == "back.smartmet.fmi.fi"; \
	    assert _derive_cluster_domain("open1.smartmet.fmi.fi") == "smartmet.fmi.fi"; \
	    assert _derive_cluster_domain("localhost") is None; \
	    assert _cluster_name_from_prefixes(["c1","c2","c3","c4","c5","c6","v1.q3","v2.q3"]) == "c", "back cluster"; \
	    assert _cluster_name_from_prefixes(["in1","in2","in3","in4"]) == "in", "internal cluster"; \
	    assert _cluster_name_from_prefixes(["open1","open2","open3"]) == "open", "opendata cluster"; \
	    assert _cluster_name_from_prefixes([]) is None; \
	    assert _cluster_name_from_prefixes(["v1.q3","v2.q3"]) is None, "all dotted prefixes"; \
	    from smartmet_webmon.clusters import ClusterRegistry, ClusterConfig; \
	    _reg = ClusterRegistry(); \
	    _reg.add(ClusterConfig(name="back", frontend_url="http://x/", admin_url_pattern="http://{prefix}.x:8081/admin")); \
	    assert _reg.names() == ["back"] and _reg.get("back") is not None and _reg.get("nope") is None; \
	    from smartmet_webmon.handlers import cluster_urls_chart, cluster_plugins_chart, cluster_keys_chart, cluster_overview_chart, CLUSTER_ROUTES, ROUTES, caches_cluster_chart, services_cluster_chart, _plugin_label; \
	    [(_r in CLUSTER_ROUTES) or (_ for _ in ()).throw(SystemExit(f"missing route {_r}")) for _r in ("/cluster/urls/chart", "/cluster/plugins/chart", "/cluster/keys/chart", "/cluster/overview/chart")]; \
	    assert "/caches/cluster_chart" in ROUTES and "/services/cluster_chart" in ROUTES; \
	    _st, _b = cluster_urls_chart(_reg, {"cluster": "back"}); \
	    assert _st == 400, (_st, _b); \
	    _st, _b = cluster_urls_chart(_reg, {"cluster": "nope", "url": "/foo"}); \
	    assert _st == 404, (_st, _b); \
	    _st, _b = cluster_urls_chart(_reg, {"cluster": "back", "url": "/foo"}); \
	    assert _st == 200 and _b["series"] == [] and _b["metric"] == "p95_ms", (_st, _b); \
	    _st, _b = caches_cluster_chart(_wst, {}); \
	    assert _st == 200 and _b["series"] == [] and "cache_names" in _b, (_st, _b); \
	    _st, _b = services_cluster_chart(_wst, {}); \
	    assert _st == 200 and _b["series"] == [] and "handlers" in _b, (_st, _b); \
	    _st, _b = cluster_plugins_chart(_reg, {"cluster": "back"}); \
	    assert _st == 200 and _b["series"] == [] and "plugin_names" in _b, (_st, _b); \
	    _st, _b = cluster_keys_chart(_reg, {"cluster": "back"}); \
	    assert _st == 200 and _b["series"] == [] and "apikeys" in _b, (_st, _b); \
	    _st, _b = cluster_overview_chart(_reg, {"cluster": "back", "metrics": "count,mean_ms,p95_ms"}); \
	    assert _st == 200 and set(_b["charts"]) == {"count","mean_ms","p95_ms"}, (_st, _b); \
	    assert _plugin_label("/timeseries") == "timeseries" and _plugin_label("/wms/sub") == "wms" and _plugin_label("/") == ""; \
	    from smartmet_top.sources.proc import detect_role; \
	    assert detect_role("/usr/sbin/smartmetd --port=8081 --configfile /smartmet/cnf/smartmetd/clients/backend.conf") == "backend"; \
	    assert detect_role("/usr/sbin/smartmetd --port=8080 --configfile /smartmet/cnf/smartmetd/clients/frontend.conf") == "frontend"; \
	    assert detect_role("/usr/sbin/smartmetd --port=8081") == "backend", "port alone is enough"; \
	    assert detect_role("/usr/sbin/smartmetd --port=8080") == "frontend"; \
	    assert detect_role("/usr/sbin/smartmetd --configfile /etc/foo/backend.conf") == "backend", "cmdline fallback when no port"; \
	    assert detect_role("/usr/sbin/smartmetd --configfile /etc/foo/random.conf") == "unknown"; \
	    assert detect_role("/usr/sbin/smartmetd --port=9999") == "unknown", "non-conventional port"; \
	    from smartmet_top.sources.analyze import analyze, Finding, SEV_HIGH; \
	    _aregex_stack = ("main","SmartMet::Spine::SmartMetPlugin::callRequestHandler","SmartMet::P::q","std::__detail::_Compiler<X>::_M_compile"); \
	    _AStore = type("_AStore", (), { \
	        "perf_recent_stacks":     lambda self, pid: [_aregex_stack] * 10, \
	        "offcpu_recent_stacks":   lambda self, pid: [], \
	        "wakeup_recent_stacks":   lambda self, pid: [], \
	        "pagefault_recent_stacks":lambda self, pid: [], \
	        "blockflame_recent_stacks":lambda self, pid: [], \
	        "malloc_recent_stacks":   lambda self, pid: [], \
	    }); \
	    _af = analyze(_AStore(), 1); \
	    assert any(f.detector_id == "request-regex-compile" and f.severity == SEV_HIGH for f in _af), _af; \
	    from smartmet_top.snapshots.ipflow import IPFlowSnapshot, angle_for_ip, _ip_to_int; \
	    assert _ip_to_int("0.0.0.0") == 0 and _ip_to_int("255.255.255.255") == 0xFFFFFFFF; \
	    assert _ip_to_int("1.2.3.4") == ((1<<24)|(2<<16)|(3<<8)|4); \
	    assert _ip_to_int("-") == 0 and _ip_to_int("nonsense") == 0; \
	    assert angle_for_ip("0.0.0.0") == 0.0; \
	    _a1 = angle_for_ip("10.0.0.1"); _a2 = angle_for_ip("10.0.0.2"); \
	    assert 0.0 < _a2 - _a1 < 1.0, "/24 neighbours must sit at adjacent angles"; \
	    _stf = Store(); \
	    _t0 = 1700000000.0; \
	    _stf.record_request(ts=_t0+1, url="/a", dur_ms=10, nbytes=100, status=200, apikey="-", ip="1.2.3.4"); \
	    _stf.record_request(ts=_t0+2, url="/b", dur_ms=20, nbytes=200, status=200, apikey="-", ip="1.2.3.4"); \
	    _stf.record_request(ts=_t0+3, url="/c", dur_ms=30, nbytes=300, status=500, apikey="-", ip="5.6.7.8"); \
	    _stf.record_request(ts=_t0+4, url="/d", dur_ms=40, nbytes=400, status=200, apikey="-"); \
	    _tl = IPFlowSnapshot.timeline(_stf, minutes=60); \
	    assert _tl["minute_step"] == 60 and len(_tl["buckets"]) == 1 and _tl["buckets"][0]["reqs"] == 4 and _tl["buckets"][0]["bytes"] == 1000, _tl; \
	    _w = IPFlowSnapshot.window(_stf, start_ts=_t0, seconds=60, top_n=0); \
	    assert len(_w["requests"]) == 3, _w; \
	    assert sorted(_w["ips"].keys()) == ["1.2.3.4", "5.6.7.8"], _w["ips"]; \
	    assert _w["ips"]["1.2.3.4"]["count"] == 2 and _w["ips"]["1.2.3.4"]["bytes"] == 300; \
	    assert abs(_w["ips"]["1.2.3.4"]["angle"] - angle_for_ip("1.2.3.4")) < 1e-9; \
	    _w2 = IPFlowSnapshot.window(_stf, start_ts=_t0, seconds=60, top_n=1); \
	    assert sorted(set(r["ip"] for r in _w2["requests"])) == ["1.2.3.4"], "top_n=1 keeps only the busiest IP"; \
	    assert sorted(_w2["ips"].keys()) == ["1.2.3.4", "5.6.7.8"], "summary still complete"; \
	    from smartmet_webmon.handlers import ipflow_timeline as _itl, ipflow_window as _iwd; \
	    _st_t, _b_t = _itl(_stf, {"minutes": "60"}); \
	    assert _st_t == 200 and _b_t["minute_step"] == 60 and _b_t["buckets"][0]["reqs"] == 4; \
	    _st_w, _b_w = _iwd(_stf, {"start": str(_t0), "seconds": "60"}); \
	    assert _st_w == 200 and len(_b_w["requests"]) == 3, (_st_w, _b_w); \
	    _st_w2, _b_w2 = _iwd(_stf, {"start": "abc"}); \
	    assert _st_w2 == 400, (_st_w2, _b_w2); \
	    _stf2 = Store(); \
	    _stf2.record_request(ts=_t0+1, url="/a", dur_ms=10, nbytes=100, status=200, apikey="-", source_label="wms", ip="1.2.3.4"); \
	    _stf2.record_request(ts=_t0+2, url="/b", dur_ms=20, nbytes=200, status=200, apikey="-", source_label="wms", ip="1.2.3.5"); \
	    _stf2.record_request(ts=_t0+3, url="/c", dur_ms=30, nbytes=300, status=200, apikey="-", source_label="timeseries", ip="1.2.3.6"); \
	    assert sorted(_stf2.ipflow_sources()) == ["timeseries", "wms"], _stf2.ipflow_sources(); \
	    _all = IPFlowSnapshot.window(_stf2, start_ts=_t0, seconds=60); \
	    assert len(_all["requests"]) == 3 and all(r["src"] in ("wms","timeseries") for r in _all["requests"]); \
	    _wms = IPFlowSnapshot.window(_stf2, start_ts=_t0, seconds=60, source="wms"); \
	    assert len(_wms["requests"]) == 2 and all(r["src"] == "wms" for r in _wms["requests"]); \
	    _ts = IPFlowSnapshot.window(_stf2, start_ts=_t0, seconds=60, source="timeseries"); \
	    assert len(_ts["requests"]) == 1 and _ts["requests"][0]["src"] == "timeseries"; \
	    _tlall = IPFlowSnapshot.timeline(_stf2, minutes=60); \
	    assert _tlall["sources"] == ["timeseries", "wms"] and _tlall["buckets"][0]["reqs"] == 3; \
	    _tlwms = IPFlowSnapshot.timeline(_stf2, minutes=60, source="wms"); \
	    assert _tlwms["buckets"][0]["reqs"] == 2; \
	    _tlnone = IPFlowSnapshot.timeline(_stf2, minutes=60, source="missing"); \
	    assert _tlnone["buckets"] == []; \
	    _stsh, _btsh = _itl(_stf2, {"source": "wms"}); \
	    assert _stsh == 200 and _btsh["source"] == "wms"; \
	    _swh, _bwh = _iwd(_stf2, {"start": str(_t0), "source": "wms"}); \
	    assert _swh == 200 and len(_bwh["requests"]) == 2; \
	    from smartmet_webmon.handlers import panels as _panels_h; \
	    _ps_st, _ps_b = _panels_h(_stf, {}); \
	    assert any(p["id"] == "ipflow" for p in _ps_b["panels"]), _ps_b; \
	    _idx = open("share/smartmet/webmon/index.html").read(); \
	    assert "ipflow.js" in _idx, "ipflow.js must be loaded by index.html"; \
	    _ifjs = open("share/smartmet/webmon/ipflow.js").read(); \
	    assert "smIPFlow" in _ifjs and "IPFlowAnimator" in _ifjs, "ipflow.js must export smIPFlow"; \
	    from smartmet_top.sources.geo import CountryDB, parse_delegated, _ipv4_to_int, _ipv6_to_int; \
	    assert _ipv4_to_int("0.0.0.0") == 0 and _ipv4_to_int("255.255.255.255") == 0xFFFFFFFF; \
	    assert _ipv4_to_int("256.0.0.0") is None and _ipv4_to_int("a.b.c.d") is None; \
	    assert _ipv6_to_int("::1") == 1 and _ipv6_to_int("::") == 0; \
	    assert _ipv6_to_int("2001:db8::") == ((0x2001 << 112) | (0xdb8 << 96)); \
	    assert _ipv6_to_int("garbage::xx") is None; \
	    _sample = chr(10).join(["2|x|0|0|0|0|+0000", "x|*|ipv4|*|0|summary", "x|FI|ipv4|193.166.0.0|65536|19920510|allocated", "x|US|ipv4|3.0.0.0|256|19920521|allocated", "x|FI|ipv4|10.10.10.10|10|20200101|reserved", "x|JP|ipv6|2001:db8::|32|20200101|allocated", ""]); \
	    _recs = list(parse_delegated(_sample)); \
	    _expected = sorted([(4, 0xC1A60000, 0xC1A6FFFF, "FI"), (4, 0x03000000, 0x030000FF, "US"), (6, 0x20010DB8 << 96, ((0x20010DB8 + 1) << 96) - 1, "JP")]); \
	    assert sorted(_recs) == _expected, _recs; \
	    import tempfile as _tf, os as _os; \
	    _tmp = _tf.NamedTemporaryFile("w", prefix="delegated-", suffix="-extended-latest", delete=False); \
	    _tmp.write(_sample); _tmp.close(); \
	    _cdb = CountryDB(); _cdb.load([_tmp.name]); \
	    assert _cdb.lookup("193.166.1.1") == "FI", _cdb.lookup("193.166.1.1"); \
	    assert _cdb.lookup("3.0.0.5") == "US"; \
	    assert _cdb.lookup("10.10.10.10") == "??", "reserved entries are skipped"; \
	    assert _cdb.lookup("127.0.0.1") == "??"; \
	    assert _cdb.lookup("2001:db8::1") == "JP"; \
	    assert bool(_cdb); \
	    _os.unlink(_tmp.name); \
	    from smartmet_top.snapshots.countries import CountriesSnapshot; \
	    _stc = Store(); \
	    assert CountriesSnapshot.status(_stc)["enabled"] is False; \
	    assert CountriesSnapshot.timeline(_stc)["series"] == []; \
	    _stc.country_db = _cdb; \
	    _t1 = 1700000060.0; \
	    _stc.record_request(ts=_t1, url="/", dur_ms=1, nbytes=100, status=200, apikey="-", ip="193.166.1.1"); \
	    _stc.record_request(ts=_t1, url="/", dur_ms=2, nbytes=200, status=500, apikey="-", ip="3.0.0.5"); \
	    _stc.record_request(ts=_t1, url="/", dur_ms=3, nbytes=300, status=200, apikey="-", ip="3.0.0.6"); \
	    _ct = CountriesSnapshot.table(_stc, minutes=60); \
	    assert _ct["rows"][0]["cc"] in ("US", "FI") and _ct["rows"][0]["reqs"] >= 1, _ct["rows"]; \
	    assert any(r["cc"] == "US" and r["reqs"] == 2 for r in _ct["rows"]); \
	    _ctl = CountriesSnapshot.timeline(_stc, minutes=60, top_n=8); \
	    _labels = sorted(s["label"] for s in _ctl["series"]); \
	    assert "US" in _labels and "FI" in _labels, _labels; \
	    from smartmet_webmon.handlers import countries_status as _cs, countries_table as _ctab, countries_timeline as _ctlh; \
	    assert _cs(_stc, {})[1]["enabled"]; \
	    assert _ctab(_stc, {})[0] == 200; \
	    assert _ctlh(_stc, {})[0] == 200; \
	    _ipw = IPFlowSnapshot.window(_stc, start_ts=_t1 - 30, seconds=60); \
	    assert _ipw["ips"]["193.166.1.1"]["cc"] == "FI" and _ipw["ips"]["3.0.0.5"]["cc"] == "US", _ipw["ips"]'
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
