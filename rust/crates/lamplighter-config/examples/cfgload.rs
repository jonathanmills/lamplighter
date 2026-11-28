// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills

//! cfgload (Rust side): load the named configuration modules from a file
//! and print what the C version (rust/difftest/c/cfgload.c) prints: "OK"
//! and one line per module, or "ERROR <message>" with exit code 1.
//! Warnings go to stderr as the C logging code writes them.

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
    if args.len() != 3 {
        eprintln!("usage: cfgload MODULES FILE");
        std::process::exit(2);
    }
    let wanted: Vec<String> = args[1].to_string_lossy().split(',').map(str::to_string).collect();
    let wanted: Vec<&str> = wanted.iter().map(String::as_str).collect();
    let mut out = std::io::stdout();
    match lamplighter_config::load::load(args[2].as_bytes(), &wanted) {
        Err(e) => {
            let _ = out.write_all(b"ERROR ");
            let _ = out.write_all(&esc(&e.0));
            let _ = out.write_all(b"\n");
            let _ = out.flush();
            std::process::exit(1);
        }
        Ok(l) => {
            let _ = out.write_all(b"OK\n");
            for w in &wanted {
                let line = match *w {
                    "global" => l.global.as_ref().map(|c| c.dump()),
                    "log" => l.log.as_ref().map(|c| c.dump()),
                    "updt" => l.updt.as_ref().map(|c| c.dump()),
                    "lmgr" => l.lmgr.as_ref().map(|c| c.dump()),
                    _ => None,
                };
                if let Some(line) = line {
                    let _ = out.write_all(&line);
                }
            }
        }
    }
}
