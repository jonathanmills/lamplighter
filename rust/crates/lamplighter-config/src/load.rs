// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// Translated from Robinhood's src/cfg_parsing/rbh_cfg.c
// (Copyright (C) CEA/DAM, CeCILL-C).

//! rbh_cfg_load(): parse the file, then load each module in order (its
//! defaults, its reader, then apply it). A reader's error stops loading
//! with "Error <rc> reading <module> configuration:\n<message>". The Log
//! module, once applied, changes how later warnings are displayed.

use crate::access::Config;
use crate::cat;
use crate::helpers::CfgError;
use crate::logs::LogState;
use crate::modules::{self, GlobalConfig, LmgrConfig, LogConfig, UpdtParams};

/// The settings the modules produced (the C code's globals).
#[derive(Default)]
pub struct Loaded {
    pub global: Option<GlobalConfig>,
    pub log: Option<LogConfig>,
    pub updt: Option<UpdtParams>,
    pub lmgr: Option<LmgrConfig>,
    /// log_config as the C code would hold it (initial value, then Log's)
    pub log_state: LogState,
}

/// The modules, in rbh_cfg.c's order, with the C module names.
pub const MODULES: &[(&str, &str)] = &[("global", "global"), ("log", "logs"), ("updt", "updt params"), ("lmgr", "list manager")];

/// Why loading failed: rbh_cfg_load's message.
pub struct LoadError(pub Vec<u8>);

/// Load the named modules (a subset of MODULES, in that order) from a file.
pub fn load(path: &[u8], wanted: &[&str]) -> Result<Loaded, LoadError> {
    let parsed = crate::parse_file(path);
    // the parser's warnings (undefined variables) print as the C parser does
    for w in &parsed.warnings {
        use std::io::Write;
        let _ = std::io::stderr().write_all(w);
    }
    let items = match parsed.result {
        Ok(items) => items,
        Err(crate::ParseError::Syntax(m)) => return Err(LoadError(m)),
        Err(crate::ParseError::Fatal(m)) => {
            eprintln!("{m}");
            std::process::exit(2);
        }
    };
    let cfg = Config::new(items);
    let mut out = Loaded::default();
    for (key, cname) in MODULES {
        if !wanted.contains(key) {
            continue;
        }
        let state = out.log_state.clone();
        let r: Result<(), CfgError> = match *key {
            "global" => modules::read_global(&cfg, &state).map(|c| out.global = Some(c)),
            "log" => modules::read_log(&cfg, &state).map(|c| {
                out.log_state = LogState { debug_level: c.debug_level, log_process: c.log_process };
                out.log = Some(c);
            }),
            "updt" => modules::read_updt(&cfg, &state).map(|c| out.updt = Some(c)),
            "lmgr" => modules::read_lmgr(&cfg, &state).map(|c| out.lmgr = Some(c)),
            _ => unreachable!(),
        };
        if let Err(e) = r {
            return Err(LoadError(cat!(format!("Error {} reading {} configuration:\n", e.rc, cname), e.msg)));
        }
    }
    Ok(out)
}
