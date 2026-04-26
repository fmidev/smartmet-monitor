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

.PHONY: all install uninstall clean check rpm

all:
	@echo "smartmet-monitor is a no-build package. Use 'make install' or 'make rpm'."

BTOOLS = bstat bchart burls bstatus bkeys
LEGACY = bstat1s bstat10s bstat1 bstat10 bstat60 bstat24
MANPAGES = smtop.1 bstat.1 bchart.1 burls.1 bstatus.1 bkeys.1 \
           bstat1s.1 bstat10s.1 bstat1.1 bstat10.1 bstat60.1 bstat24.1

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
	$(foreach m,$(MANPAGES),rm -f $(MANDIR)/$(m); )
	rm -rf $(DOCDIR)
	rm -rf $(SITEDIR)

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
	    assert _majflt_rate([]) == 0.0 and _majflt_rate([s[0]]) == 0.0'
	$(PYTHON) -m py_compile smartmet_top/*.py smartmet_top/*/*.py
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

clean:
	find . -name __pycache__ -prune -exec rm -rf {} +

# Build RPM from HEAD. Uses ~/.rpmmacros for %_topdir, matching other
# smartmet-* repos in this hub (e.g. macgyver, spine).
NAME = smartmet-monitor
VERSION = $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' smartmet_top/__init__.py)

rpm: clean $(NAME).spec
	rm -f $(NAME)-$(VERSION).tar.gz
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ HEAD \
	    -o $(NAME)-$(VERSION).tar.gz
	rpmbuild -tb $(NAME)-$(VERSION).tar.gz
	rm -f $(NAME)-$(VERSION).tar.gz
