#!/bin/bash
# SPDX-License-Identifier: CECILL-C
# Copyright (C) 2026 Jonathan Mills
#
# Build the C reference for the side-by-side tests (doc/RUST-PLAN.md): the
# engine exactly as tagged (default v1.0.0), POSIX mode like the dev build,
# with a fixed SOURCE_DATE_EPOCH so build dates are reproducible. Runs in
# the dev workbench (git, compilers, /src read-only, /build writable).
# Cached per tagged commit: a second run does nothing.
#
# Usage: build_reference.sh [tag]   ->  /build/reference/<tag>/

set -euo pipefail
tag=${1:-v1.0.0}
src=/src
out=/build/reference/$tag
export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1795824000}    # 2026-11-28 00:00:00 UTC

commit=$(git -c safe.directory="$src" -C "$src" rev-parse "$tag^{commit}")
if [[ -f $out/.built && $(cat "$out/.built") == "$commit" ]]; then
    echo "reference $tag ($commit) already built in $out"
    exit 0
fi

echo "building the C reference $tag ($commit) in $out"
rm -rf -- "$out"
mkdir -p "$out"
git -c safe.directory="$src" -C "$src" archive "$commit" | tar -x -C "$out"
cd "$out"
sh autogen.sh >/dev/null 2>&1
# --disable-rust: the reference is the C engine (later tags contain Rust)
./configure --disable-lustre $(grep -q 'enable-rust\|disable-rust' configure && echo --disable-rust) >/dev/null
make -j"$(nproc)" >/dev/null 2>&1 || { make 2>&1 | tail -30; exit 1; }
echo "$commit" > .built
echo "reference $tag built in $out"
