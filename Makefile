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
	# README
	install -m 0644 README.md $(DOCDIR)/README.md
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
	    from smartmet_top.widgets.bars import sparkline, vchart, set_ascii; \
	    set_ascii(False); assert sparkline([0,1,2,3,4,5,6,7,8], width=4); \
	    set_ascii(True);  assert sparkline([0,1,2,3,4,5,6,7,8], width=4); \
	    set_ascii(False); assert len(vchart([0,1,2,3,4,5,6,7,8], 3)) == 3; \
	    from smartmet_top.sources.perftop import parse_perf_script; \
	    assert parse_perf_script("smartmetd 1 [0] 1.0:    99 cycles:\n    deadbeef foo+0x0 (lib.so)\n\n")[0] == ("foo",)'
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
