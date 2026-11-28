// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
//
// This software is governed by the CeCILL-C license under French law and
// abiding by the rules of distribution of free software (see LICENSE).

//! Pieces shared by Lamplighter's Rust tools.
//!
//! - [`build`]: what configure decided (version, release, build date,
//!   Lustre and changelog support) and the `--version` banner, which must
//!   match the C tools' output exactly.
//! - [`names`]: user and group names from the system's databases.

pub mod build;
pub mod names;
