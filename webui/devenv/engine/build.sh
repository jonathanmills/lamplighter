#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Build Lamplighter's engine (POSIX mode) inside the workbench. The read-only
# repository at /src is copied to /build/lamplighter, so build artifacts
# never land in the macOS checkout. Re-runs are incremental.

set -euo pipefail

src=/src
dst=/build/lamplighter

mkdir -p "$dst"
rsync -a --delete \
      --exclude .git --exclude webui --exclude rpms \
      --filter 'P configure' --filter 'P Makefile.in' --filter 'P Makefile' \
      --filter 'P aclocal.m4' --filter 'P autom4te.cache/' --filter 'P config.*' \
      --filter 'P *.o' --filter 'P *.lo' --filter 'P *.la' --filter 'P .libs/' \
      --filter 'P .deps/' --filter 'P libtool' --filter 'P stamp-h1' \
      --filter 'P autotools/' \
      "$src/" "$dst/"
cd "$dst"

# Generate the build system if it is missing. When configure.ac or a
# Makefile.am changes later, make regenerates it with autotools/missing,
# which the rsync filter above keeps.
if [[ ! -x configure || ! -x autotools/missing ]]; then
    sh autogen.sh
    rm -f Makefile
fi
if [[ ! -f Makefile ]]; then
    ./configure --disable-lustre
fi
make -j"$(nproc)"

echo "lux-dev: lamplighter built in $dst"
