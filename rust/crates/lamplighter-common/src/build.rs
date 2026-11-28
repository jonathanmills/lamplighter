// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// The banner reproduces Robinhood's display_version() (src/robinhood/*.c),
// Copyright (C) 2008-2017 CEA/DAM, CeCILL-C.

//! Build information, passed in by `make` (rust/Makefile.am) from what
//! configure decided, so the Rust and C tools of one build agree.

/// `PACKAGE_NAME` in the C build.
pub const PACKAGE: &str = "lamplighter";
/// `PACKAGE_VERSION` (configure.ac); the crate version outside `make`.
pub const VERSION: &str = match option_env!("LAMPLIGHTER_VERSION") {
    Some(v) => v,
    None => env!("CARGO_PKG_VERSION"),
};
/// `RELEASE` (configure.ac).
pub const RELEASE: &str = match option_env!("LAMPLIGHTER_RELEASE") {
    Some(v) => v,
    None => "0",
};
/// `COMPIL_DATE`: UTC, "%F %T", from SOURCE_DATE_EPOCH when set.
pub const BUILD_DATE: &str = match option_env!("LAMPLIGHTER_BUILD_DATE") {
    Some(v) => v,
    None => "unknown",
};
/// `PACKAGE_BUGREPORT` (configure.ac).
pub const BUGREPORT: &str = "https://github.com/jonathanmills/lamplighter/issues";
/// `LUSTRE_VERSION`, when configure found one (make passes "" otherwise).
pub fn lustre_version() -> Option<&'static str> {
    match option_env!("LAMPLIGHTER_LUSTRE_VERSION") {
        Some(v) if !v.is_empty() => Some(v),
        _ => None,
    }
}

/// The `--version` text of a tool, byte for byte as the C tools print it.
/// `product` is what follows the package name, e.g. `"'du' command"`
/// (`None` for the daemon, which prints the package name alone).
pub fn version_banner(product: Option<&str>) -> String {
    let mut s = String::from("\n");
    match product {
        Some(p) => s += &format!("Product:         {PACKAGE} {p}\n"),
        None => s += &format!("Product:         {PACKAGE}\n"),
    }
    s += &format!("Version:         {VERSION}-{RELEASE}\n");
    s += &format!("Build:           {BUILD_DATE}\n");
    s += "\nCompilation switches:\n";
    s += if cfg!(feature = "fid") { "    Address entries by FID\n" } else { "    Address entries by path\n" };
    s += if cfg!(feature = "changelogs") { "    MDT Changelogs supported\n" } else { "    MDT Changelogs disabled\n" };
    s += "\n";
    if cfg!(feature = "lustre") {
        match lustre_version() {
            Some(v) => s += &format!("Lustre Version: {v}\n"),
            None => s += "Lustre FS support\n",
        }
    } else {
        s += "No Lustre support\n";
    }
    s += "Database binding: MySQL\n\n";
    s += &format!("Report bugs to: <{BUGREPORT}>\n\n");
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn banner_has_the_c_layout() {
        let b = version_banner(Some("'du' command"));
        let lines: Vec<&str> = b.lines().collect();
        assert_eq!(lines[0], "");
        assert_eq!(lines[1], "Product:         lamplighter 'du' command");
        assert!(lines[2].starts_with("Version:         ") && lines[2].contains('-'));
        assert_eq!(lines[5], "Compilation switches:");
        assert!(b.ends_with(">\n\n"));
        assert!(b.contains("Database binding: MySQL\n"));
    }
}
