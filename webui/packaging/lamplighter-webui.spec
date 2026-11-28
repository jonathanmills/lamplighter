# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Lamplighter web UI. Make the SRPM from a git checkout with
# webui/packaging/make-srpm.sh (or "make webui-srpm"), then
# "rpmbuild --rebuild" it on any Rocky Linux / RHEL 9 build host, of either
# architecture. The front end is built here with Node.js 18 or newer
# (BuildRequires only: the installed RPM never needs Node.js). npm and pip
# download packages, so the build host needs internet access or mirrors.
# %%install creates the virtualenv at its real path (/opt/lamplighter-webui),
# because virtualenvs record absolute paths: build on a host where
# /opt/lamplighter-webui is not in use.

%global venv /opt/lamplighter-webui
%global debug_package %{nil}
%global __brp_python_bytecompile %{nil}
# Everything under the venv is private: no automatic Requires/Provides from it.
%global __requires_exclude_from ^%{venv}/.*$
%global __provides_exclude_from ^%{venv}/.*$

Name:           lamplighter-webui
Version:        1.0.0
Release:        1%{?dist}
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

install -d %{buildroot}%{_datadir}/lamplighter-webui
cp -a frontend/dist %{buildroot}%{_datadir}/lamplighter-webui/frontend

install -D -m 0644 packaging/lamplighter-webui.service           %{buildroot}%{_unitdir}/lamplighter-webui.service
install -D -m 0644 packaging/lamplighter-collector.service %{buildroot}%{_unitdir}/lamplighter-collector.service
install -D -m 0644 packaging/lamplighter-collector.timer   %{buildroot}%{_unitdir}/lamplighter-collector.timer
install -D -m 0644 packaging/lamplighter-jobstats.service  %{buildroot}%{_unitdir}/lamplighter-jobstats.service
install -D -m 0644 packaging/lamplighter-jobstats.timer    %{buildroot}%{_unitdir}/lamplighter-jobstats.timer
install -D -m 0644 packaging/lamplighter-reports.service   %{buildroot}%{_unitdir}/lamplighter-reports.service
install -D -m 0644 packaging/lamplighter-reports.timer     %{buildroot}%{_unitdir}/lamplighter-reports.timer
install -d %{buildroot}%{_localstatedir}/spool/lamplighter-jobstats
install -D -m 0644 packaging/lamplighter-webui.sysusers          %{buildroot}%{_sysusersdir}/lamplighter-webui.conf
install -D -m 0640 packaging/config.yaml.example         %{buildroot}%{_sysconfdir}/lamplighter.d/webui/config.yaml
# Templates are regular files, not %%doc: minimal installs skip documentation.
install -D -m 0644 packaging/nginx-lamplighter-webui.conf        %{buildroot}%{_datadir}/lamplighter-webui/nginx-lamplighter-webui.conf.example
install -D -m 0644 packaging/config.yaml.example         %{buildroot}%{_datadir}/lamplighter-webui/config.yaml.example

%pre
getent group lamplighter-webui >/dev/null || groupadd -r lamplighter-webui
getent passwd lamplighter-webui >/dev/null || \
    useradd -r -g lamplighter-webui -d /var/lib/lamplighter-webui -s /sbin/nologin -c "Lamplighter web UI" lamplighter-webui
# Accounts that deliver jobstats snapshots join lamplighter-jobstats, never lamplighter-webui.
getent group lamplighter-jobstats >/dev/null || groupadd -r lamplighter-jobstats
id -nG lamplighter-webui | grep -qw lamplighter-jobstats || usermod -a -G lamplighter-jobstats lamplighter-webui
exit 0

%post
%systemd_post lamplighter-webui.service lamplighter-collector.timer lamplighter-jobstats.timer lamplighter-reports.timer

%preun
%systemd_preun lamplighter-webui.service lamplighter-collector.service lamplighter-collector.timer lamplighter-jobstats.service lamplighter-jobstats.timer lamplighter-reports.service lamplighter-reports.timer

%postun
%systemd_postun_with_restart lamplighter-webui.service

%files
%license LICENSE
%doc packaging/README.md
%{venv}
%{_datadir}/lamplighter-webui
%{_unitdir}/lamplighter-webui.service
%{_unitdir}/lamplighter-collector.service
%{_unitdir}/lamplighter-collector.timer
%{_unitdir}/lamplighter-jobstats.service
%{_unitdir}/lamplighter-jobstats.timer
%{_unitdir}/lamplighter-reports.service
%{_unitdir}/lamplighter-reports.timer
# setgid: delivered files stay group lamplighter-jobstats
%dir %attr(2770,lamplighter-webui,lamplighter-jobstats) %{_localstatedir}/spool/lamplighter-jobstats
%{_sysusersdir}/lamplighter-webui.conf
# /etc/lamplighter.d is shared with the engine packages (both own the directory)
%dir %{_sysconfdir}/lamplighter.d
%dir %attr(0750,root,lamplighter-webui) %{_sysconfdir}/lamplighter.d/webui
%config(noreplace) %attr(0640,root,lamplighter-webui) %{_sysconfdir}/lamplighter.d/webui/config.yaml

%changelog
* Sat Nov 28 2026 Jonathan Mills - 1.0.0-1
- Lamplighter 1.0.0: feature-complete with Robinhood's PHP GUI (see
  doc/RELEASE-NOTES.md).

* Sat Nov 28 2026 Jonathan Mills - 0.1.0-5
- lamplighter-reports service and timer (scheduled reports, every 15 minutes).

* Fri Nov 27 2026 Jonathan Mills - 0.1.0-4
- Lamplighter naming: package, user, units, /opt/lamplighter-webui,
  configuration in /etc/lamplighter.d/webui, lamplighter_webui schema.

* Fri Nov 27 2026 Jonathan Mills - 0.1.0-3
- The SRPM carries the front-end source, built by rpmbuild with Node.js
  on the build host, so make-srpm.sh needs no containers.

* Fri Nov 27 2026 Jonathan Mills - 0.1.0-2
- lamplighter-jobstats service and timer, and the jobstats spool directory.

* Thu Nov 26 2026 Jonathan Mills - 0.1.0-1
- First package: back end, front end, collector timer, nginx example.
