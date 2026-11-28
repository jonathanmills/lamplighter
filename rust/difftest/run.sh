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

# The C cfgdump: Robinhood's parser from the reference, printing its trees
mkdir -p /build/difftest-c
gcc -O1 -Wall -I"$ref/src/include" -I"$ref/src/cfg_parsing" $(pkg-config --cflags glib-2.0) \
    -o /build/difftest-c/cfgdump "$here/c/cfgdump.c" \
    "$ref/src/cfg_parsing/.libs/libconfigparsing.a" $(pkg-config --libs glib-2.0)

# Environment variables some configuration cases refer to ($VAR values and
# %include "$VAR"); both sides see the same environment.
export LL_TEST_VAR="value from the environment"
export LL_TEST_INCLUDE="$here/config-cases/inc/child.inc"
unset LL_TEST_UNDEFINED

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

src=$(cd "$here/../.." && pwd)
# Stage 1: the Rust parser's tables must be Bison's for the committed grammar
python3.12 "$src/rust/tools/gen_parser_tables.py" --check \
    "$src/src/cfg_parsing/conf_yacc.c" "$src/rust/crates/lamplighter-config/src/tables.rs"

python3.12 "$here/difftest.py" --ref "$ref" --cur "$cur" --rust "$target/release" "$here"/cases/*.toml "$@"

# Stage 1: differential fuzzing of the configuration parser (mutants run
# next to the include files, so relative %include statements resolve)
(cd "$here/config-cases/inc" && python3.12 "$here/fuzz_config.py" \
    --cdump /build/difftest-c/cfgdump --rdump "$target/release/examples/cfgdump" \
    --iterations "${FUZZ_ITERATIONS:-10000}" --seed "${FUZZ_SEED:-1}" \
    "$here/config-cases/*.conf" "$src/doc/templates/**/*.conf" "$src/tests/**/*.conf")
