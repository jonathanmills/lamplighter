// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills

//! Prints the `--version` banner, for the side-by-side tests
//! (rust/difftest): `version_banner "'du' command"` must print exactly
//! what the C `lux-du --version` prints in the same build.

fn main() {
    let product = std::env::args().nth(1);
    print!("{}", lamplighter_common::build::version_banner(product.as_deref()));
}
