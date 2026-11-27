#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Build the lamplighter-webui source RPM from a git checkout. Needs only
# rpm-build and tar: no containers, no Node.js, no Python packages.
#
#   webui/packaging/make-srpm.sh            (or: make webui-srpm)
#   rpmbuild --rebuild webui/packaging/dist/lamplighter-webui-*.src.rpm
#
# The SRPM carries the front-end *source*; rpmbuild --rebuild builds it
# with Node.js 18 or newer on the build host (Rocky Linux 9:
# dnf module enable nodejs:22). Node.js is never needed on the servers
# that run Lamplighter. The rebuild downloads Python and npm packages, so
# the build host needs internet access (or local mirrors).

set -euo pipefail
cd "$(dirname "$0")"
webui=$(cd .. && pwd)
version=$(sed -n 's/^Version: *//p' lamplighter-webui.spec)
name=lamplighter-webui-$version
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

src="$work/$name"
mkdir -p "$src/backend" "$src/frontend" "$src/packaging" dist
cp -a "$webui/backend/pyproject.toml" "$webui/backend/LICENSE" "$webui/backend/src" "$src/backend/"
find "$src/backend" \( -name __pycache__ -o -name "*.egg-info" \) -prune -exec rm -rf {} +
cp -a "$webui/frontend/package.json" "$webui/frontend/package-lock.json" "$webui/frontend/tsconfig.json" \
      "$webui/frontend/vite.config.ts" "$webui/frontend/index.html" "$webui/frontend/src" \
      "$webui/frontend/public" "$src/frontend/"
cp README.md config.yaml.example nginx-lux-webui.conf lux-*.service lux-*.timer lux-webui.sysusers \
   "$src/packaging/"
cp lamplighter-webui.spec "$webui/LICENSE" "$src/"

# No macOS metadata ("._*" files) in the tarball: rpmbuild would see two spec files.
COPYFILE_DISABLE=1 tar --no-mac-metadata -C "$work" -czf "$work/$name.tar.gz" "$name" 2>/dev/null \
    || tar -C "$work" -czf "$work/$name.tar.gz" "$name"
rpmbuild -ts "$work/$name.tar.gz" --define "_topdir $work/rpm" --define "_srcrpmdir $PWD/dist" \
    | sed 's/^Wrote: /wrote /'
