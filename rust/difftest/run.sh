#!/bin/bash
# SPDX-License-Identifier: CECILL-C
# Copyright (C) 2026 Jonathan Mills
#
# Run the side-by-side tests in the dev workbench (devenv.sh rust-test):
#   1. build the C reference (v1.0.0, cached) and test data
#   2. build the Rust workspace's binaries and examples with the reference's
#      build information (version, release, the same fixed build date)
#   3. compare reference and candidate for every case in cases/*.toml
# Expects the current engine built in /build/lamplighter (devenv.sh build).

set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
tag=${REFERENCE_TAG:-v1.0.0}
export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1795824000}
ref=/build/reference/$tag
cur=/build/lamplighter
target=/build/difftest-target

bash "$here/build_reference.sh" "$tag"
bash "$here/setup.sh" "$ref"

# The Rust side, built with the reference's build information so banners
# and versions compare equal (configure's PACKAGE_VERSION and RELEASE).
version=$(sed -n 's/^PACKAGE_VERSION = //p' "$ref/Makefile")
release=$(sed -n 's/^RELEASE = //p' "$ref/Makefile")
echo "building the Rust candidates ($version-$release)"
(cd "$cur/rust" && \
 LAMPLIGHTER_VERSION="$version" LAMPLIGHTER_RELEASE="$release" \
 LAMPLIGHTER_BUILD_DATE="$(date --utc --date "@$SOURCE_DATE_EPOCH" '+%F %T')" \
 LAMPLIGHTER_LUSTRE_VERSION="" CARGO_TARGET_DIR="$target" \
 cargo build -q --release --locked --workspace --examples)

python3.12 "$here/difftest.py" --ref "$ref" --cur "$cur" --rust "$target/release" "$here"/cases/*.toml "$@"
