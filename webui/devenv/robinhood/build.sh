#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Build Robinhood (POSIX mode) inside the workbench. The read-only
# repository at /src is copied to /build/robinhood, so build artifacts
# never land in the macOS checkout. Re-runs are incremental.

set -euo pipefail

src=/src
dst=/build/robinhood

mkdir -p "$dst"
rsync -a --delete \
      --exclude .git --exclude webui --exclude rpms \
      --filter 'P configure' --filter 'P Makefile.in' --filter 'P Makefile' \
      --filter 'P aclocal.m4' --filter 'P autom4te.cache/' --filter 'P config.*' \
      --filter 'P *.o' --filter 'P *.lo' --filter 'P *.la' --filter 'P .libs/' \
      --filter 'P .deps/' --filter 'P libtool' --filter 'P stamp-h1' \
      "$src/" "$dst/"
cd "$dst"

if [[ ! -x configure ]]; then
    sh autogen.sh
fi
if [[ ! -f Makefile ]]; then
    ./configure --disable-lustre
fi
make -j"$(nproc)"

echo "lux-dev: robinhood built in $dst"
