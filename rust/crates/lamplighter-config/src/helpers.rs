// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// Translated from Robinhood's src/cfg_parsing/rbh_cfg_helpers.c
// (Copyright (C) CEA/DAM, CeCILL-C).

//! Typed parameter getters (Get*Param), block lookups and the unknown
//! parameter check, with the C code's messages, return codes and order of
//! checks: the first failing check decides the message.

use crate::access::{self, Config, Kind};
use crate::cvt;
use crate::tree::Item;

pub const ENOENT: i32 = 2;
pub const EEXIST: i32 = 17;
pub const EINVAL: i32 = 22;

pub const MANDATORY: u32 = 1 << 0;
pub const ABSOLUTE_PATH: u32 = 1 << 1;
pub const REMOVE_FINAL_SLASH: u32 = 1 << 2;
pub const NO_WILDCARDS: u32 = 1 << 3;
pub const MAIL: u32 = 1 << 4;
pub const STDIO_ALLOWED: u32 = 1 << 5;
pub const POSITIVE: u32 = 1 << 6;
pub const NOT_NULL: u32 = 1 << 7;
pub const NOT_EMPTY: u32 = NOT_NULL;
pub const ALLOW_PCT_SIGN: u32 = 1 << 8;

/// An error code (errno) and the message the C code writes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CfgError {
    pub rc: i32,
    pub msg: Vec<u8>,
}

impl CfgError {
    pub fn new(rc: i32, msg: Vec<u8>) -> CfgError {
        CfgError { rc, msg }
    }
    pub fn inval(msg: Vec<u8>) -> CfgError {
        CfgError { rc: EINVAL, msg }
    }
}

/// Concatenate byte strings (the C code's sprintf of %s arguments).
#[macro_export]
macro_rules! cat {
    ($($part:expr),* $(,)?) => {{
        let mut v: Vec<u8> = Vec::new();
        $( v.extend_from_slice(AsRef::<[u8]>::as_ref(&$part)); )*
        v
    }};
}

fn s(b: &[u8]) -> &[u8] {
    crate::lexer::cstr(b)
}

/// The value of a parameter and its extra arguments, when allowed.
pub struct Param<T> {
    pub value: T,
    pub extra: Vec<Vec<u8>>,
}

struct Found<'a> {
    item: &'a Item,
    value: &'a [u8],
    extra: bool,
}

fn get_cfg_param<'a>(cfg: &Config, block: &'a Item, block_name: &[u8], var: &[u8], flags: u32) -> Result<Found<'a>, CfgError> {
    let mut unique = true;
    let Some(item) = cfg.get_item_by_name(block, var, &mut unique) else {
        let msg = if flags & MANDATORY != 0 {
            cat!(b"Missing mandatory parameter '", s(var), b"' in block '", s(block_name), b"', line ",
                 access::line(block).to_string())
        } else {
            Vec::new()
        };
        return Err(CfgError::new(ENOENT, msg));
    };
    if !unique {
        return Err(CfgError::new(EEXIST, cat!(b"Duplicate definition of parameter '", s(var), b"' found in block '",
            s(block_name), b"', line ", access::line(item).to_string(), b".")));
    }
    match access::key_value(item) {
        Some((_, value, extra)) => Ok(Found { item, value, extra }),
        None => Err(CfgError::new(-1, cat!(b"Error retrieving parameter value for '", s(block_name), b"::", s(var),
            b"', line ", access::line(item).to_string(), b":\n", access::NOT_A_VAR))),
    }
}

fn line_of(f: &Found) -> String {
    access::line(f.item).to_string()
}

fn where_(block_name: &[u8], var: &[u8], f: &Found) -> Vec<u8> {
    cat!(b"'", s(block_name), b"::", s(var), b"', line ", line_of(f))
}

fn extra_args(f: &Found, allow_extra: bool, block_name: &[u8], var: &[u8]) -> Result<Vec<Vec<u8>>, CfgError> {
    if !f.extra {
        return Ok(Vec::new());
    }
    if !allow_extra {
        return Err(CfgError::inval(cat!(b"Unexpected options for parameter ", where_(block_name, var, f))));
    }
    Ok(access::extra_args(f.item))
}

fn wildcards_in(v: &[u8]) -> bool {
    v.iter().any(|c| b"*?[]{}".contains(c))
}

/// GetStringParam; `target_size` is the C buffer size.
pub fn get_string(cfg: &Config, block: &Item, block_name: &[u8], var: &[u8], flags: u32, target_size: usize,
                  allow_extra: bool) -> Result<Param<Vec<u8>>, CfgError> {
    let f = get_cfg_param(cfg, block, block_name, var, flags)?;
    let value = s(f.value);
    if value.len() >= target_size {
        return Err(CfgError::inval(cat!(b"Option too long for parameter ", where_(block_name, var, &f))));
    }
    let mut target = value.to_vec();
    let extra = extra_args(&f, allow_extra, block_name, var)?;
    if flags & NOT_EMPTY != 0 && target.is_empty() {
        return Err(CfgError::inval(cat!(b"Unexpected empty parameter ", where_(block_name, var, &f))));
    }
    if flags & STDIO_ALLOWED != 0 && ["stdout", "stderr", "syslog"].iter().any(|n| cvt::eq_nocase(&target, n.as_bytes())) {
        return Ok(Param { value: target, extra });
    }
    if flags & ABSOLUTE_PATH != 0 && target.first() != Some(&b'/') {
        return Err(CfgError::inval(cat!(b"Absolute path expected for parameter ", where_(block_name, var, &f))));
    }
    if flags & NO_WILDCARDS != 0 && wildcards_in(&target) {
        return Err(CfgError::inval(cat!(b"Wildcards are not allowed in ", where_(block_name, var, &f))));
    }
    if flags & MAIL != 0 {
        let at = target.iter().position(|&c| c == b'@');
        if at.map_or(true, |i| i == 0 || i + 1 == target.len()) {
            return Err(CfgError::inval(cat!(b"Invalid mail address in ", where_(block_name, var, &f))));
        }
    }
    if flags & REMOVE_FINAL_SLASH != 0 && target.len() > 1 && target.last() == Some(&b'/') {
        target.pop();
    }
    Ok(Param { value: target, extra })
}

/// GetBoolParam
pub fn get_bool(cfg: &Config, block: &Item, block_name: &[u8], var: &[u8], flags: u32, allow_extra: bool)
    -> Result<Param<bool>, CfgError> {
    let f = get_cfg_param(cfg, block, block_name, var, flags)?;
    let b = cvt::str2bool(f.value);
    if b == -1 {
        return Err(CfgError::inval(cat!(b"Invalid value for ", where_(block_name, var, &f),
            b": boolean expected (0, 1, true, false, yes, no, enabled, disabled)")));
    }
    let extra = extra_args(&f, allow_extra, block_name, var)?;
    Ok(Param { value: b != 0, extra })
}

/// GetDurationParam (time_t, from str2duration's int)
pub fn get_duration(cfg: &Config, block: &Item, block_name: &[u8], var: &[u8], flags: u32, allow_extra: bool)
    -> Result<Param<i64>, CfgError> {
    let f = get_cfg_param(cfg, block, block_name, var, flags)?;
    let t = cvt::str2duration(f.value) as i64;
    if t == -1 {
        return Err(CfgError::inval(cat!(b"Invalid value for ", where_(block_name, var, &f), b": duration expected. Eg: 10s")));
    }
    if flags & POSITIVE != 0 && t < 0 {
        return Err(CfgError::inval(cat!(b"Positive value expected for ", where_(block_name, var, &f), b".")));
    }
    if flags & NOT_NULL != 0 && t == 0 {
        return Err(CfgError::inval(cat!(b"'", s(block_name), b"::", s(var), b"' must not be null, line ", line_of(&f), b".")));
    }
    let extra = extra_args(&f, allow_extra, block_name, var)?;
    Ok(Param { value: t, extra })
}

/// GetSizeParam
pub fn get_size(cfg: &Config, block: &Item, block_name: &[u8], var: &[u8], flags: u32, allow_extra: bool)
    -> Result<Param<u64>, CfgError> {
    let f = get_cfg_param(cfg, block, block_name, var, flags)?;
    let v = cvt::str2size(f.value);
    if v == u64::MAX {
        return Err(CfgError::inval(cat!(b"Invalid value for ", where_(block_name, var, &f), b": size expected. Eg: 10MB")));
    }
    if flags & NOT_NULL != 0 && v == 0 {
        return Err(CfgError::inval(cat!(b"'", s(block_name), b"::", s(var), b"' must not be null, line ", line_of(&f), b".")));
    }
    let extra = extra_args(&f, allow_extra, block_name, var)?;
    Ok(Param { value: v, extra })
}

/// GetIntParam
pub fn get_int(cfg: &Config, block: &Item, block_name: &[u8], var: &[u8], flags: u32, allow_extra: bool)
    -> Result<Param<i32>, CfgError> {
    let f = get_cfg_param(cfg, block, block_name, var, flags)?;
    let (nb, v, rest) = cvt::scan_int(f.value);
    if nb < 1 {
        return Err(CfgError::inval(cat!(b"Invalid value for ", where_(block_name, var, &f), b": integer expected.")));
    }
    if nb > 1 && !rest.is_empty() {
        return Err(CfgError::inval(cat!(b"Invalid value for ", where_(block_name, var, &f), b": extra characters '", rest,
            b"' found after integer ", v.to_string(), b".")));
    }
    if flags & POSITIVE != 0 && v < 0 {
        return Err(CfgError::inval(cat!(b"Positive value expected for ", where_(block_name, var, &f), b".")));
    }
    if flags & NOT_NULL != 0 && v == 0 {
        return Err(CfgError::inval(cat!(b"'", s(block_name), b"::", s(var), b"' must not be null, line ", line_of(&f), b".")));
    }
    let extra = extra_args(&f, allow_extra, block_name, var)?;
    Ok(Param { value: v, extra })
}

/// GetInt64Param (with the k, M, G, T suffixes: powers of 1000)
pub fn get_int64(cfg: &Config, block: &Item, block_name: &[u8], var: &[u8], flags: u32, allow_extra: bool)
    -> Result<Param<u64>, CfgError> {
    let f = get_cfg_param(cfg, block, block_name, var, flags)?;
    let (nb, mut v, rest) = cvt::scan_u64(f.value);
    if nb < 1 {
        return Err(CfgError::inval(cat!(b"Invalid value for ", where_(block_name, var, &f), b": integer expected.")));
    }
    if nb > 1 && !rest.is_empty() {
        let m: u64 = if cvt::eq_nocase(&rest, b"k") {
            1_000
        } else if cvt::eq_nocase(&rest, b"M") {
            1_000_000
        } else if cvt::eq_nocase(&rest, b"G") {
            1_000_000_000
        } else if cvt::eq_nocase(&rest, b"T") {
            1_000_000_000_000
        } else {
            return Err(CfgError::inval(cat!(b"Invalid suffix for ", where_(block_name, var, &f), b": '", rest,
                b"'. Only 'k', 'M', 'G' or 'T' are allowed.")));
        };
        v = v.wrapping_mul(m);
    }
    if flags & NOT_NULL != 0 && v == 0 {
        return Err(CfgError::inval(cat!(b"'", s(block_name), b"::", s(var), b"' must not be null, line ", line_of(&f), b".")));
    }
    let extra = extra_args(&f, allow_extra, block_name, var)?;
    Ok(Param { value: v, extra })
}

/// GetFloatParam
pub fn get_float(cfg: &Config, block: &Item, block_name: &[u8], var: &[u8], flags: u32, allow_extra: bool)
    -> Result<Param<f64>, CfgError> {
    let f = get_cfg_param(cfg, block, block_name, var, flags)?;
    let (nb, v, rest) = cvt::scan_float(f.value);
    if nb < 1 {
        return Err(CfgError::inval(cat!(b"Invalid value for ", where_(block_name, var, &f), b": float expected.")));
    }
    if nb > 1 {
        let pct = flags & ALLOW_PCT_SIGN != 0;
        if (!pct && !rest.is_empty()) || (pct && rest != b"%") {
            return Err(CfgError::inval(cat!(b"Invalid value for ", where_(block_name, var, &f), b": extra characters '", rest,
                b"' found after float ", cvt::fmt_2f(v), b".")));
        }
    }
    if flags & POSITIVE != 0 && v < 0.0 {
        return Err(CfgError::inval(cat!(b"Positive value expected for ", where_(block_name, var, &f), b".")));
    }
    if flags & NOT_NULL != 0 && v == 0.0 {
        return Err(CfgError::inval(cat!(b"'", s(block_name), b"::", s(var), b"' must not be null, line ", line_of(&f), b".")));
    }
    let extra = extra_args(&f, allow_extra, block_name, var)?;
    Ok(Param { value: v, extra })
}

/// cfg_is_err: a missing optional parameter is not an error.
pub fn optional<T>(r: Result<Param<T>, CfgError>, flags: u32) -> Result<Option<T>, CfgError> {
    match r {
        Ok(p) => Ok(Some(p.value)),
        Err(e) if e.rc == ENOENT && flags & MANDATORY == 0 => Ok(None),
        Err(e) => Err(e),
    }
}

fn check_block<'a>(item: Option<&'a Item>, name: &[u8], unique: bool) -> Result<&'a Item, CfgError> {
    let Some(item) = item else {
        return Err(CfgError::new(ENOENT, cat!(b"Missing configuration block '", s(name), b"'")));
    };
    if !unique {
        return Err(CfgError::new(EEXIST, cat!(b"Found duplicate of block '", s(name), b"' line ",
            access::line(item).to_string(), b".")));
    }
    if access::kind(item) != Kind::Block {
        return Err(CfgError::inval(cat!(b"A block is expected for '", s(name), b"' item, line ",
            access::line(item).to_string())));
    }
    Ok(item)
}

/// get_cfg_block
pub fn get_block<'a>(cfg: &'a Config, name: &[u8]) -> Result<&'a Item, CfgError> {
    let mut unique = true;
    let item = cfg.find_item_by_name(name, &mut unique);
    check_block(item, name, unique)
}

/// get_cfg_subblock
pub fn get_subblock<'a>(cfg: &Config, block: &'a Item, name: &[u8]) -> Result<&'a Item, CfgError> {
    let mut unique = true;
    let item = cfg.get_item_by_name(block, name, &mut unique);
    check_block(item, name, unique)
}

/// CheckUnknownParameters: the warnings it logs (LVL_CRIT, "Config Check").
pub fn unknown_parameters(cfg: &Config, block: &Item, block_name: &[u8], allowed: &[&str]) -> Vec<Vec<u8>> {
    let mut out = Vec::new();
    for item in access::content(block) {
        if cfg.is_read(item) {
            continue;
        }
        let known = |n: &[u8]| allowed.iter().any(|a| cvt::eq_nocase(a.as_bytes(), n));
        match access::kind(item) {
            Kind::Var => {
                if let Some((n, _, _)) = access::key_value(item) {
                    if !known(n) {
                        out.push(cat!(b"WARNING: unknown parameter '", s(n), b"' in block '", s(block_name), b"' line ",
                            access::line(item).to_string()));
                    }
                }
            }
            Kind::Block => {
                if let Some(n) = access::name(item) {
                    if !known(n) {
                        out.push(cat!(b"WARNING: unknown block '", s(n), b"' as sub-block of '", s(block_name), b"' line ",
                            access::line(item).to_string()));
                    }
                }
            }
            _ => {}
        }
    }
    out
}
