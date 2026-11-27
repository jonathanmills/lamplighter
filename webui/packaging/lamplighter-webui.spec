# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Lamplighter web UI. Make the SRPM from a git checkout with
# webui/packaging/make-srpm.sh (or "make webui-srpm"), then
# "rpmbuild --rebuild" it on any Rocky Linux / RHEL 9 build host, of either
# architecture. The front end is built here with Node.js 18 or newer
# (BuildRequires only: the installed RPM never needs Node.js). npm and pip
# download packages, so the build host needs internet access or mirrors.
# %%install creates the virtualenv at its real path (/opt/lux-webui),
# because virtualenvs record absolute paths: build on a host where
# /opt/lux-webui is not in use.

%global venv /opt/lux-webui
%global debug_package %{nil}
%global __brp_python_bytecompile %{nil}
# Everything under the venv is private: no automatic Requires/Provides from it.
%global __requires_exclude_from ^%{venv}/.*$
%global __provides_exclude_from ^%{venv}/.*$

Name:           lamplighter-webui
Version:        0.1.0
Release:        3%{?dist}
Summary:        Web UI and monitoring API for Lamplighter
License:        GPL-2.0-or-later
URL:            https://github.com/jonathanmills/lamplighter
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  python3.12 python3.12-devel python3.12-pip gcc openldap-devel systemd-rpm-macros
# Rocky Linux 9's default nodejs is 16: dnf module enable nodejs:22 first.
BuildRequires:  nodejs >= 1:18
BuildRequires:  npm
Requires:       python3.12 openldap
Requires(pre):  shadow-utils
%{?systemd_requires}
Recommends:     nginx

%description
A web interface for Lamplighter: dashboards, usage per user
and group, cold-data views for data managers, directory browsing, and LDAP-
and Keycloak-based access control. Includes the nightly collector and a
monitoring API polled by Checkmk. Reads the Robinhood-format MariaDB database; it
never writes to it. No PHP.

%prep
%setup -q

%build
# The front end: static files, built once here. Node.js stays on the build host.
cd frontend
npm ci --no-audit --no-fund --loglevel=error
npm run build

%install
rm -rf %{venv}
python3.12 -m venv %{venv}
%{venv}/bin/pip install --no-cache-dir --upgrade pip
%{venv}/bin/pip install --no-cache-dir ./backend
%{venv}/bin/python -m compileall -q %{venv}/lib
mkdir -p %{buildroot}/opt
cp -a %{venv} %{buildroot}/opt/

install -d %{buildroot}%{_datadir}/lux-webui
cp -a frontend/dist %{buildroot}%{_datadir}/lux-webui/frontend

install -D -m 0644 packaging/lux-webui.service           %{buildroot}%{_unitdir}/lux-webui.service
install -D -m 0644 packaging/lux-collector.service %{buildroot}%{_unitdir}/lux-collector.service
install -D -m 0644 packaging/lux-collector.timer   %{buildroot}%{_unitdir}/lux-collector.timer
install -D -m 0644 packaging/lux-jobstats.service  %{buildroot}%{_unitdir}/lux-jobstats.service
install -D -m 0644 packaging/lux-jobstats.timer    %{buildroot}%{_unitdir}/lux-jobstats.timer
install -d %{buildroot}%{_localstatedir}/spool/lux-jobstats
install -D -m 0644 packaging/lux-webui.sysusers          %{buildroot}%{_sysusersdir}/lux-webui.conf
install -D -m 0640 packaging/config.yaml.example         %{buildroot}%{_sysconfdir}/lux-webui/config.yaml
# Templates are regular files, not %%doc: minimal installs skip documentation.
install -D -m 0644 packaging/nginx-lux-webui.conf        %{buildroot}%{_datadir}/lux-webui/nginx-lux-webui.conf.example
install -D -m 0644 packaging/config.yaml.example         %{buildroot}%{_datadir}/lux-webui/config.yaml.example

%pre
getent group lux-webui >/dev/null || groupadd -r lux-webui
getent passwd lux-webui >/dev/null || \
    useradd -r -g lux-webui -d /var/lib/lux-webui -s /sbin/nologin -c "Lamplighter web UI" lux-webui
# Accounts that deliver jobstats snapshots join lux-jobstats, never lux-webui.
getent group lux-jobstats >/dev/null || groupadd -r lux-jobstats
id -nG lux-webui | grep -qw lux-jobstats || usermod -a -G lux-jobstats lux-webui
exit 0

%post
%systemd_post lux-webui.service lux-collector.timer lux-jobstats.timer

%preun
%systemd_preun lux-webui.service lux-collector.service lux-collector.timer lux-jobstats.service lux-jobstats.timer

%postun
%systemd_postun_with_restart lux-webui.service

%files
%license LICENSE
%doc packaging/README.md
%{venv}
%{_datadir}/lux-webui
%{_unitdir}/lux-webui.service
%{_unitdir}/lux-collector.service
%{_unitdir}/lux-collector.timer
%{_unitdir}/lux-jobstats.service
%{_unitdir}/lux-jobstats.timer
# setgid: delivered files stay group lux-jobstats
%dir %attr(2770,lux-webui,lux-jobstats) %{_localstatedir}/spool/lux-jobstats
%{_sysusersdir}/lux-webui.conf
%dir %attr(0750,root,lux-webui) %{_sysconfdir}/lux-webui
%config(noreplace) %attr(0640,root,lux-webui) %{_sysconfdir}/lux-webui/config.yaml

%changelog
* Fri Nov 27 2026 Jonathan Mills - 0.1.0-3
- The SRPM carries the front-end source, built by rpmbuild with Node.js
  on the build host, so make-srpm.sh needs no containers.

* Fri Nov 27 2026 Jonathan Mills - 0.1.0-2
- lux-jobstats service and timer, and the jobstats spool directory.

* Thu Nov 26 2026 Jonathan Mills - 0.1.0-1
- First package: back end, front end, collector timer, nginx example.
