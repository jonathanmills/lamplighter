# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Robinhood web UI. Build with webui/packaging/build-rpm.sh, which builds
# the front end (Node, in a container) and runs rpmbuild in a throwaway
# Rocky Linux container. %%install creates the virtualenv at its real path
# (/opt/rbh-webui) because virtualenvs record absolute paths, so never run
# this spec on a machine where /opt/rbh-webui matters.

%global venv /opt/rbh-webui
%global debug_package %{nil}
%global __brp_python_bytecompile %{nil}
# Everything under the venv is private: no automatic Requires/Provides from it.
%global __requires_exclude_from ^%{venv}/.*$
%global __provides_exclude_from ^%{venv}/.*$

Name:           robinhood-webui
Version:        0.1.0
Release:        1%{?dist}
Summary:        Web UI and monitoring API for the Robinhood Policy Engine
License:        GPL-2.0-or-later
URL:            https://github.com/jonathanmills/robinhood
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  python3.12 python3.12-devel python3.12-pip gcc openldap-devel systemd-rpm-macros
Requires:       python3.12 openldap
Requires(pre):  shadow-utils
%{?systemd_requires}
Recommends:     nginx

%description
A web interface for the Robinhood Policy Engine: dashboards, usage per user
and group, cold-data views for data managers, directory browsing, and LDAP-
and Keycloak-based access control. Includes the nightly collector and a
monitoring API polled by Checkmk. Reads Robinhood's MariaDB database; it
never writes to it. No PHP.

%prep
%setup -q

%build
# Nothing to compile here: the front end is pre-built (frontend-dist/), and
# the Python environment is assembled in %%install.

%install
rm -rf %{venv}
python3.12 -m venv %{venv}
%{venv}/bin/pip install --no-cache-dir --upgrade pip
%{venv}/bin/pip install --no-cache-dir ./backend
%{venv}/bin/python -m compileall -q %{venv}/lib
mkdir -p %{buildroot}/opt
cp -a %{venv} %{buildroot}/opt/

install -d %{buildroot}%{_datadir}/rbh-webui
cp -a frontend-dist %{buildroot}%{_datadir}/rbh-webui/frontend

install -D -m 0644 packaging/rbh-webui.service           %{buildroot}%{_unitdir}/rbh-webui.service
install -D -m 0644 packaging/rbh-webui-collector.service %{buildroot}%{_unitdir}/rbh-webui-collector.service
install -D -m 0644 packaging/rbh-webui-collector.timer   %{buildroot}%{_unitdir}/rbh-webui-collector.timer
install -D -m 0644 packaging/rbh-webui.sysusers          %{buildroot}%{_sysusersdir}/rbh-webui.conf
install -D -m 0640 packaging/config.yaml.example         %{buildroot}%{_sysconfdir}/rbh-webui/config.yaml
# Templates are regular files, not %%doc: minimal installs skip documentation.
install -D -m 0644 packaging/nginx-rbh-webui.conf        %{buildroot}%{_datadir}/rbh-webui/nginx-rbh-webui.conf.example
install -D -m 0644 packaging/config.yaml.example         %{buildroot}%{_datadir}/rbh-webui/config.yaml.example

%pre
getent group rbh-webui >/dev/null || groupadd -r rbh-webui
getent passwd rbh-webui >/dev/null || \
    useradd -r -g rbh-webui -d /var/lib/rbh-webui -s /sbin/nologin -c "Robinhood web UI" rbh-webui
exit 0

%post
%systemd_post rbh-webui.service rbh-webui-collector.timer

%preun
%systemd_preun rbh-webui.service rbh-webui-collector.service rbh-webui-collector.timer

%postun
%systemd_postun_with_restart rbh-webui.service

%files
%license LICENSE
%doc packaging/README.md
%{venv}
%{_datadir}/rbh-webui
%{_unitdir}/rbh-webui.service
%{_unitdir}/rbh-webui-collector.service
%{_unitdir}/rbh-webui-collector.timer
%{_sysusersdir}/rbh-webui.conf
%dir %attr(0750,root,rbh-webui) %{_sysconfdir}/rbh-webui
%config(noreplace) %attr(0640,root,rbh-webui) %{_sysconfdir}/rbh-webui/config.yaml

%changelog
* Thu Nov 26 2026 Jonathan Mills - 0.1.0-1
- First package: back end, front end, collector timer, nginx example.
