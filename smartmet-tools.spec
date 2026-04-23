%global _python3_sitelib %{python3_sitelib}

Name:           smartmet-tools
Version:        0.1.0
Release:        1%{?dist}
Summary:        Command-line analysis and monitoring tools for SmartMet Server
License:        MIT
URL:            https://github.com/fmidev/smartmet-tools
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3 >= 3.9
BuildRequires:  make

Requires:       python3 >= 3.9

%description
Two companion tools for operating a SmartMet Server:

  * bstat, bchart, burls, bstatus, bkeys — Bash/awk functions for
    analysing access-log files offline. Sourced automatically into
    interactive login shells from /etc/profile.d/smartmet-tools.sh.

  * smtop — an interactive curses dashboard (like btop) that tails
    access logs and polls the admin plugin for cache statistics,
    service statistics, active requests and more. Supports multiple
    hosts and auto-detects whether each admin URL belongs to a
    frontend or backend node.

Both parts are implemented with the Python 3 standard library; no pip
packages are required at runtime.

%prep
%setup -q

%build
# Nothing to compile — Python stdlib only. The Makefile's check target
# validates byte-compilation across all modules.
make check PYSITELIB=%{_python3_sitelib}

%install
rm -rf %{buildroot}
make install \
    DESTDIR=%{buildroot} \
    PREFIX=%{_prefix} \
    PYSITELIB=%{_python3_sitelib}

%files
%{_bindir}/smtop
%{_bindir}/smartmet-top
%{_datadir}/smartmet/bstat.sh
/etc/profile.d/smartmet-tools.sh
%{_python3_sitelib}/smartmet_top/
%{_python3_sitelib}/smartmet_top/*/

%changelog
* Thu Apr 23 2026 Mika Heiskanen <mika.heiskanen@fmi.fi> - 0.1.0-1
- Initial release. Bundles bstat and the new smtop dashboard.
