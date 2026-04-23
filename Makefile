PREFIX ?= /usr
DESTDIR ?=

# Resolve the system Python 3 site-packages dir (e.g. /usr/lib/python3.9/site-packages).
# Override PYSITELIB= at invocation to force a specific path (the RPM does this).
PYSITELIB ?= $(shell python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')

BINDIR = $(DESTDIR)$(PREFIX)/bin
SHAREDIR = $(DESTDIR)$(PREFIX)/share/smartmet
MANDIR = $(DESTDIR)$(PREFIX)/share/man/man1
DOCDIR = $(DESTDIR)$(PREFIX)/share/doc/smartmet-monitor
SITEDIR = $(DESTDIR)$(PYSITELIB)/smartmet_top

.PHONY: all install uninstall clean check rpm rpm-sources

all:
	@echo "smartmet-monitor is a no-build package. Use 'make install' or 'make rpm'."

BTOOLS = bstat bchart burls bstatus bkeys
MANPAGES = smtop.1 bstat.1 bchart.1 burls.1 bstatus.1 bkeys.1

install:
	install -d $(BINDIR) $(SHAREDIR) $(MANDIR) $(DOCDIR)
	install -d $(SITEDIR) $(SITEDIR)/panels $(SITEDIR)/sources $(SITEDIR)/state $(SITEDIR)/widgets
	# smtop plus bstat-family command wrappers
	install -m 0755 smtop $(BINDIR)/smtop
	$(foreach t,$(BTOOLS),install -m 0755 bin/$(t) $(BINDIR)/$(t); )
	# shared library that all bstat-family wrappers source
	install -m 0644 share/smartmet/bstat.sh $(SHAREDIR)/bstat.sh
	# python package
	install -m 0644 smartmet_top/*.py         $(SITEDIR)/
	install -m 0644 smartmet_top/panels/*.py  $(SITEDIR)/panels/
	install -m 0644 smartmet_top/sources/*.py $(SITEDIR)/sources/
	install -m 0644 smartmet_top/state/*.py   $(SITEDIR)/state/
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
	rm -f $(SHAREDIR)/bstat.sh
	$(foreach m,$(MANPAGES),rm -f $(MANDIR)/$(m); )
	rm -rf $(DOCDIR)
	rm -rf $(SITEDIR)

check:
	python3 -c 'import sys; sys.path.insert(0, "."); import smartmet_top, smartmet_top.app'
	python3 -m py_compile smartmet_top/*.py smartmet_top/*/*.py
	bash -n share/smartmet/bstat.sh
	for t in $(BTOOLS); do bash -n bin/$$t; done
	# End-to-end: each wrapper must load the library and print at least
	# its --help section without crashing.
	for t in $(BTOOLS); do \
	    SMARTMET_MONITOR_LIB=$(CURDIR)/share/smartmet/bstat.sh \
	        bin/$$t --help >/dev/null || exit 1; \
	done

clean:
	find . -name __pycache__ -prune -exec rm -rf {} +

# Build RPM from the working tree. Requires rpmbuild.
NAME = smartmet-monitor
VERSION = $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' smartmet_top/__init__.py)

rpm-sources:
	rm -rf rpmbuild && mkdir -p rpmbuild/SOURCES rpmbuild/SPECS
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ HEAD \
		-o rpmbuild/SOURCES/$(NAME)-$(VERSION).tar.gz
	cp smartmet-monitor.spec rpmbuild/SPECS/

rpm: rpm-sources
	rpmbuild --define "_topdir $(CURDIR)/rpmbuild" -bb rpmbuild/SPECS/smartmet-monitor.spec
