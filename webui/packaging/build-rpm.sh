#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Build the robinhood-webui RPM for Rocky Linux / RHEL 9 without installing
# anything on this machine: the front end is built in a Node container and
# the RPM in a throwaway Rocky Linux 9.7 container. Output: packaging/dist/
#
# Usage: webui/packaging/build-rpm.sh   (needs docker or podman)

set -euo pipefail
cd "$(dirname "$0")"
here=$PWD
webui=$(cd .. && pwd)
version=$(sed -n 's/^Version: *//p' robinhood-webui.spec)
name=robinhood-webui-$version
export DOCKER_CONFIG=$webui/devenv/.docker-config   # ignore desktop credential helpers
mkdir -p "$DOCKER_CONFIG"; [[ -f $DOCKER_CONFIG/config.json ]] || echo '{}' > "$DOCKER_CONFIG/config.json"

echo "== front end"
docker run --rm -v "$webui/frontend:/app" -v rbh-dev_rbh-node-modules:/app/node_modules -w /app \
    docker.io/library/node:22-alpine sh -c "npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null"

echo "== source tarball"
rm -rf .build && mkdir -p ".build/$name" dist
cp -a "$webui/backend" ".build/$name/backend"
rm -rf ".build/$name/backend/tests" ".build/$name/backend/.pytest_cache"
find ".build/$name/backend" -name __pycache__ -prune -exec rm -rf {} +
cp -a "$webui/frontend/dist" ".build/$name/frontend-dist"
mkdir -p ".build/$name/packaging"
cp README.md config.yaml.example nginx-rbh-webui.conf rbh-webui*.service rbh-webui-collector.timer \
   rbh-webui.sysusers ".build/$name/packaging/"
cp robinhood-webui.spec "$webui/LICENSE" ".build/$name/"
# no macOS metadata ("._*" files): rpmbuild would see two spec files
COPYFILE_DISABLE=1 tar --no-mac-metadata -C .build -czf ".build/$name.tar.gz" "$name" 2>/dev/null \
    || tar -C .build -czf ".build/$name.tar.gz" "$name"

echo "== rpmbuild (Rocky Linux 9.7)"
docker run --rm -v "$here/.build:/src:ro" -v "$here/dist:/out" docker.io/rockylinux/rockylinux:9.7 bash -c "
    set -e
    dnf -y -q install rpm-build python3.12 python3.12-devel python3.12-pip gcc openldap-devel \
        systemd-rpm-macros shadow-utils >/dev/null
    rpmbuild -ta /src/$name.tar.gz --define '_topdir /tmp/rpm' >/tmp/rpmbuild.log 2>&1 || { tail -40 /tmp/rpmbuild.log; exit 1; }
    cp /tmp/rpm/RPMS/*/*.rpm /tmp/rpm/SRPMS/*.rpm /out/
"
rm -rf .build
ls -la dist/
