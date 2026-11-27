#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Maintainers' shortcut: build the lamplighter-webui SRPM and RPM in a
# throwaway Rocky Linux 9.7 container, without installing anything on this
# machine. It runs exactly what a sysadmin runs on a build host:
#   make-srpm.sh, dnf builddep, rpmbuild --rebuild
# Output: packaging/dist/.  Usage: webui/packaging/build-rpm.sh  (docker or podman)

set -euo pipefail
cd "$(dirname "$0")"
webui=$(cd .. && pwd)
export DOCKER_CONFIG=$webui/devenv/.docker-config   # ignore desktop credential helpers
mkdir -p "$DOCKER_CONFIG" dist; [[ -f $DOCKER_CONFIG/config.json ]] || echo '{}' > "$DOCKER_CONFIG/config.json"

docker run --rm -v "$webui:/webui" docker.io/rockylinux/rockylinux:9.7 bash -c '
    set -e
    dnf -y -q install rpm-build dnf-plugins-core tar >/dev/null
    dnf -y -q module enable nodejs:22 >/dev/null
    echo "== source RPM (make-srpm.sh)"
    /webui/packaging/make-srpm.sh
    srpm=$(ls -t /webui/packaging/dist/lamplighter-webui-*.src.rpm | head -1)
    echo "== build dependencies"
    dnf -y -q builddep "$srpm" >/dev/null
    echo "== rpmbuild --rebuild"
    rpmbuild --rebuild "$srpm" --define "_topdir /tmp/rpm" >/tmp/rpmbuild.log 2>&1 \
        || { tail -40 /tmp/rpmbuild.log; exit 1; }
    cp /tmp/rpm/RPMS/*/*.rpm /webui/packaging/dist/
'
ls -la dist/
