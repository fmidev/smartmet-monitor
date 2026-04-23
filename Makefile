PREFIX ?= /usr
DESTDIR ?=

# Resolve the system Python 3 site-packages dir (e.g. /usr/lib/python3.9/site-packages).
# Override PYSITELIB= at invocation to force a specific path (the RPM does this).
PYSITELIB ?= $(shell python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')

BINDIR = $(DESTDIR)$(PREFIX)/bin
SHAREDIR = $(DESTDIR)$(PREFIX)/share/smartmet
PROFILEDIR = $(DESTDIR)/etc/profile.d
SITEDIR = $(DESTDIR)$(PYSITELIB)/smartmet_top

.PHONY: all install uninstall clean check rpm rpm-sources

all:
	@echo "smartmet-tools is a no-build package. Use 'make install' or 'make rpm'."

install:
	install -d $(BINDIR) $(SHAREDIR) $(PROFILEDIR)
	install -d $(SITEDIR) $(SITEDIR)/panels $(SITEDIR)/sources $(SITEDIR)/state $(SITEDIR)/widgets
	install -m 0755 smtop $(BINDIR)/smtop
	# legacy bstat / bchart / burls / bstatus / bkeys
	install -m 0644 share/smartmet/bstat.sh $(SHAREDIR)/bstat.sh
	install -m 0644 profile.d/smartmet-tools.sh $(PROFILEDIR)/smartmet-tools.sh
	# python package
	install -m 0644 smartmet_top/*.py         $(SITEDIR)/
	install -m 0644 smartmet_top/panels/*.py  $(SITEDIR)/panels/
	install -m 0644 smartmet_top/sources/*.py $(SITEDIR)/sources/
	install -m 0644 smartmet_top/state/*.py   $(SITEDIR)/state/
	install -m 0644 smartmet_top/widgets/*.py $(SITEDIR)/widgets/
	# symlink for discoverability alongside smartmet-library-* and friends
	ln -sf smtop $(BINDIR)/smartmet-top

uninstall:
	rm -f $(BINDIR)/smtop $(BINDIR)/smartmet-top
	rm -f $(SHAREDIR)/bstat.sh
	rm -f $(PROFILEDIR)/smartmet-tools.sh
	rm -rf $(SITEDIR)

check:
	python3 -c 'import sys; sys.path.insert(0, "."); import smartmet_top, smartmet_top.app'
	python3 -m py_compile smartmet_top/*.py smartmet_top/*/*.py
	bash -n share/smartmet/bstat.sh
	bash -n profile.d/smartmet-tools.sh

clean:
	find . -name __pycache__ -prune -exec rm -rf {} +

# Build RPM from the working tree. Requires rpmbuild.
NAME = smartmet-tools
VERSION = $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' smartmet_top/__init__.py)

rpm-sources:
	rm -rf rpmbuild && mkdir -p rpmbuild/SOURCES rpmbuild/SPECS
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ HEAD \
		-o rpmbuild/SOURCES/$(NAME)-$(VERSION).tar.gz
	cp smartmet-tools.spec rpmbuild/SPECS/

rpm: rpm-sources
	rpmbuild --define "_topdir $(CURDIR)/rpmbuild" -bb rpmbuild/SPECS/smartmet-tools.spec
