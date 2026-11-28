# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Rocky Linux 9.7 workbench: builds Robinhood (POSIX mode) from the
# repository, runs scans against generated test data, and hosts the
# smoke tests (LDAP, Keycloak, MariaDB clients). Rust comes from RHEL 9's
# rust-toolset, the same toolchain the RPMs build with (doc/RUST-PLAN.md).

FROM docker.io/rockylinux/rockylinux:9.7

RUN dnf -y install epel-release dnf-plugins-core \
 && dnf config-manager --set-enabled crb \
 && dnf -y install \
        gcc make autoconf automake libtool pkgconf-pkg-config \
        bison flex perl \
        glib2-devel libattr-devel zlib-devel jemalloc-devel \
        mariadb-devel mariadb \
        python3.12 python3.12-pip \
        openldap-clients jq rsync procps-ng which file diffutils git \
        rpm-build systemd systemd-rpm-macros \
        rust-toolset cargo \
 && dnf clean all

# The workbench trusts the dev test CA (mounted at /certs at runtime).
COPY entrypoint.sh /usr/local/bin/lamplighter-dev-entrypoint
ENTRYPOINT ["/usr/local/bin/lamplighter-dev-entrypoint"]
CMD ["sleep", "infinity"]
