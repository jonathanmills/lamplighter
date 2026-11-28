// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills

//! cfgdump (Rust side): parse a configuration file and print what the C
//! version (rust/difftest/c/cfgdump.c) prints for Robinhood's C parser:
//! "OK", the tree, "PRINT" and rh_config_Print's output; or
//! "ERROR <message>" with exit code 1. Warnings go to stderr, as in C;
//! a fatal scanner error prints flex's message and exits with 2.

use std::io::Write;
use std::os::unix::ffi::OsStrExt;

fn esc(s: &[u8]) -> Vec<u8> {
    let mut out = vec![b'"'];
    for &c in s {
        if c < 0x20 || c >= 0x7f || c == b'"' || c == b'\\' {
            out.extend_from_slice(format!("\\x{c:02x}").as_bytes());
        } else {
            out.push(c);
        }
    }
    out.push(b'"');
    out
}

fn main() {
    let args: Vec<_> = std::env::args_os().collect();
    if args.len() != 2 {
        eprintln!("usage: cfgdump FILE");
        std::process::exit(2);
    }
    let parsed = lamplighter_config::parse_file(args[1].as_bytes());
    let mut err = std::io::stderr();
    for w in &parsed.warnings {
        let _ = err.write_all(w);
    }
    let mut out = std::io::stdout();
    match parsed.result {
        Ok(items) => {
            let _ = out.write_all(b"OK\n");
            let _ = out.write_all(&lamplighter_config::dump(&items));
            let _ = out.write_all(b"PRINT\n");
            let _ = out.write_all(&lamplighter_config::c_print(&items));
        }
        Err(lamplighter_config::ParseError::Syntax(msg)) => {
            let _ = out.write_all(b"ERROR ");
            let _ = out.write_all(&esc(&msg));
            let _ = out.write_all(b"\n");
            let _ = out.flush();
            std::process::exit(1);
        }
        Err(lamplighter_config::ParseError::Fatal(msg)) => {
            let _ = out.flush();
            let _ = writeln!(err, "{msg}");
            std::process::exit(2);
        }
    }
}
