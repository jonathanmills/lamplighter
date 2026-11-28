// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// Translated from Robinhood's src/common/global_config.c, rbh_logs.c,
// update_params.c and src/list_mgr/listmgr_config.c
// (Copyright (C) CEA/DAM, CeCILL-C).

//! The configuration modules every tool loads before the policies:
//! General, Log, db_update_params and ListManager. Each has the C module's
//! defaults, parameters, flags, order of reading, messages and warnings.

use crate::access::Config;
use crate::cat;
use crate::cvt::{self, eq_nocase};
use crate::helpers::{self as h, optional, CfgError, ENOENT, EINVAL};
use crate::logs::{self, LogState};
use crate::tree::Item;

const RBH_PATH_MAX: usize = 4096;
const FILENAME_MAX: usize = 4096;

fn esc(out: &mut Vec<u8>, s: &[u8]) {
    out.push(b'"');
    for &c in crate::lexer::cstr(s) {
        if c < 0x20 || c >= 0x7f || c == b'"' || c == b'\\' {
            out.extend_from_slice(format!("\\x{c:02x}").as_bytes());
        } else {
            out.push(c);
        }
    }
    out.push(b'"');
}

fn s_field(out: &mut Vec<u8>, name: &str, v: &[u8]) {
    out.extend_from_slice(format!(" {name}=").as_bytes());
    esc(out, v);
}

fn i_field(out: &mut Vec<u8>, name: &str, v: i64) {
    out.extend_from_slice(format!(" {name}={v}").as_bytes());
}

// --- General ------------------------------------------------------------------

/// fs_key_t
pub const FSKEY_FSNAME: i32 = 1;
pub const FSKEY_FSID: i32 = 2;
pub const FSKEY_DEVID: i32 = 3;

#[derive(Clone, Debug)]
pub struct GlobalConfig {
    pub fs_path: Vec<u8>,
    pub fs_type: Vec<u8>,
    pub fs_key: i32,
    pub stay_in_fs: bool,
    pub check_mounted: bool,
    pub last_access_only_atime: bool,
    pub uid_gid_as_numbers: bool,
    pub direct_mds_stat: bool,
    pub lustre_projid: bool,
}

impl Default for GlobalConfig {
    fn default() -> GlobalConfig {
        GlobalConfig {
            fs_path: Vec::new(),
            fs_type: if cfg!(feature = "lustre") { b"lustre".to_vec() } else { Vec::new() },
            fs_key: FSKEY_FSNAME,
            stay_in_fs: true,
            check_mounted: true,
            last_access_only_atime: false,
            uid_gid_as_numbers: false,
            direct_mds_stat: false,
            lustre_projid: false,
        }
    }
}

const GLOBAL: &[u8] = b"General";

pub fn read_global(cfg: &Config, log: &LogState) -> Result<GlobalConfig, CfgError> {
    let mut c = GlobalConfig::default();
    let allowed = ["fs_path", "fs_type", "stay_in_fs", "check_mounted", "direct_mds_stat", "fs_key",
                   "last_access_only_atime", "uid_gid_as_numbers", "lustre_projid"];
    let b = h::get_block(cfg, GLOBAL)?;
    // read_scalar_params, in the table's order
    let f = h::MANDATORY | h::ABSOLUTE_PATH | h::REMOVE_FINAL_SLASH | h::NO_WILDCARDS;
    if let Some(v) = optional(h::get_string(cfg, b, GLOBAL, b"fs_path", f, RBH_PATH_MAX, false), f)? {
        c.fs_path = v;
    }
    let f = if cfg!(feature = "lustre") { 0 } else { h::MANDATORY } | h::NO_WILDCARDS;
    if let Some(v) = optional(h::get_string(cfg, b, GLOBAL, b"fs_type", f, FILENAME_MAX, false), f)? {
        c.fs_type = v;
    }
    for (name, field) in [("stay_in_fs", &mut c.stay_in_fs), ("check_mounted", &mut c.check_mounted),
                          ("last_access_only_atime", &mut c.last_access_only_atime),
                          ("uid_gid_as_numbers", &mut c.uid_gid_as_numbers)] {
        if let Some(v) = optional(h::get_bool(cfg, b, GLOBAL, name.as_bytes(), 0, false), 0)? {
            *field = v;
        }
    }
    if cfg!(all(feature = "lustre", feature = "mds_stat")) {
        if let Some(v) = optional(h::get_bool(cfg, b, GLOBAL, b"direct_mds_stat", 0, false), 0)? {
            c.direct_mds_stat = v;
        }
    }
    if cfg!(feature = "lustre") {
        if let Some(v) = optional(h::get_bool(cfg, b, GLOBAL, b"lustre_projid", 0, false), 0)? {
            c.lustre_projid = v;
        }
        if c.fs_type != b"lustre" {
            return Err(CfgError::inval(b"This Lamplighter version has been built for Lustre filesystem support only".to_vec()));
        }
    }
    match h::get_string(cfg, b, GLOBAL, b"fs_key", h::NO_WILDCARDS, 128, false) {
        Ok(p) => {
            c.fs_key = if eq_nocase(&p.value, b"fsname") {
                FSKEY_FSNAME
            } else if eq_nocase(&p.value, b"fsid") {
                FSKEY_FSID
            } else if eq_nocase(&p.value, b"devid") {
                FSKEY_DEVID
            } else {
                return Err(CfgError::inval(cat!(b"Invalid type for fs_key: '", p.value,
                    b"' ('fsname', 'devid' or 'fsid' expected)")));
            };
        }
        Err(e) if e.rc == ENOENT => {}
        Err(e) => return Err(e),
    }
    for w in h::unknown_parameters(cfg, b, GLOBAL, &allowed) {
        logs::display(log, logs::LVL_CRIT, "Config Check", &w);
    }
    Ok(c)
}

impl GlobalConfig {
    pub fn dump(&self) -> Vec<u8> {
        let mut o = b"global".to_vec();
        s_field(&mut o, "fs_path", &self.fs_path);
        s_field(&mut o, "fs_type", &self.fs_type);
        i_field(&mut o, "fs_key", self.fs_key as i64);
        i_field(&mut o, "stay_in_fs", self.stay_in_fs as i64);
        i_field(&mut o, "check_mounted", self.check_mounted as i64);
        i_field(&mut o, "last_access_only_atime", self.last_access_only_atime as i64);
        i_field(&mut o, "uid_gid_as_numbers", self.uid_gid_as_numbers as i64);
        if cfg!(all(feature = "lustre", feature = "mds_stat")) {
            i_field(&mut o, "direct_mds_stat", self.direct_mds_stat as i64);
        }
        if cfg!(feature = "lustre") {
            i_field(&mut o, "lustre_projid", self.lustre_projid as i64);
        }
        o.push(b'\n');
        o
    }
}

// --- Log -----------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct LogConfig {
    pub debug_level: i32,
    pub log_file: Vec<u8>,
    pub report_file: Vec<u8>,
    pub alert_mail: Vec<u8>,
    pub alert_file: Vec<u8>,
    pub changelogs_file: Vec<u8>,
    pub syslog_facility: i32,
    pub syslog_priority: i32,
    pub batch_alert_max: i32,
    pub stats_interval: i64,
    pub alert_show_attrs: bool,
    pub log_process: bool,
    pub log_host: bool,
}

const LOG_LOCAL1: i32 = 17 << 3;
const LOG_INFO: i32 = 6;

impl Default for LogConfig {
    fn default() -> LogConfig {
        LogConfig {
            debug_level: logs::LVL_EVENT,
            log_file: b"/var/log/lamplighter.log".to_vec(),
            report_file: b"/var/log/lamplighter_actions.log".to_vec(),
            alert_mail: Vec::new(),
            alert_file: b"/var/log/lamplighter_alerts.log".to_vec(),
            changelogs_file: Vec::new(),
            syslog_facility: LOG_LOCAL1,
            syslog_priority: LOG_INFO,
            batch_alert_max: 1,
            stats_interval: 900,
            alert_show_attrs: false,
            log_process: false,
            log_host: false,
        }
    }
}

/// glibc's facilitynames (syslog.h, SYSLOG_NAMES), in order.
const FACILITIES: &[(&str, i32)] = &[
    ("auth", 4 << 3), ("authpriv", 10 << 3), ("cron", 9 << 3), ("daemon", 3 << 3), ("ftp", 11 << 3),
    ("kern", 0), ("lpr", 6 << 3), ("mail", 2 << 3), ("mark", 24 << 3), ("news", 7 << 3),
    ("security", 4 << 3), ("syslog", 5 << 3), ("user", 1 << 3), ("uucp", 8 << 3),
    ("local0", 16 << 3), ("local1", 17 << 3), ("local2", 18 << 3), ("local3", 19 << 3),
    ("local4", 20 << 3), ("local5", 21 << 3), ("local6", 22 << 3), ("local7", 23 << 3),
];
/// glibc's prioritynames, in order.
const PRIORITIES: &[(&str, i32)] = &[
    ("alert", 1), ("crit", 2), ("debug", 7), ("emerg", 0), ("err", 3), ("error", 3), ("info", 6),
    ("none", 0x10), ("notice", 5), ("panic", 0), ("warn", 4), ("warning", 4),
];

/// str2debuglevel
pub fn str2debuglevel(s: &[u8]) -> i32 {
    for (n, v) in [("CRIT", logs::LVL_CRIT), ("MAJOR", logs::LVL_MAJOR), ("EVENT", logs::LVL_EVENT),
                   ("VERB", logs::LVL_VERB), ("DEBUG", logs::LVL_DEBUG), ("FULL", logs::LVL_FULL)] {
        if eq_nocase(s, n.as_bytes()) {
            return v;
        }
    }
    -1
}

/// check_syslog_facility: (facility, priority) or ENOENT
fn syslog_facility(descr: &[u8], fac: &mut i32, prio: &mut i32) -> Result<(), ()> {
    let d = crate::lexer::strncpy(descr, 256);
    let (f, p) = match d.iter().position(|&c| c == b'.') {
        Some(i) => (&d[..i], Some(&d[i + 1..])),
        None => (&d[..], None),
    };
    *fac = FACILITIES.iter().find(|(n, _)| eq_nocase(n.as_bytes(), f)).ok_or(())?.1;
    if let Some(p) = p {
        *prio = PRIORITIES.iter().find(|(n, _)| eq_nocase(n.as_bytes(), p)).ok_or(())?.1;
    }
    Ok(())
}

const LOG: &[u8] = b"Log";

pub fn read_log(cfg: &Config, log: &LogState) -> Result<LogConfig, CfgError> {
    let mut c = LogConfig::default();
    let mut allowed = vec!["debug_level", "log_file", "report_file", "alert_file", "alert_mail", "stats_interval",
                           "batch_alert_max", "alert_show_attrs", "syslog_facility", "log_procname", "log_hostname"];
    if cfg!(feature = "changelogs") {
        allowed.push("changelogs_file");
    }
    let b = match h::get_block(cfg, LOG) {
        Ok(b) => b,
        Err(e) if e.rc == ENOENT => return Ok(c),
        Err(e) => return Err(e),
    };
    let fpath = h::ABSOLUTE_PATH | h::NO_WILDCARDS | h::STDIO_ALLOWED;
    if let Some(v) = optional(h::get_string(cfg, b, LOG, b"log_file", fpath, RBH_PATH_MAX, false), fpath)? {
        c.log_file = v;
    }
    if let Some(v) = optional(h::get_string(cfg, b, LOG, b"report_file", fpath, RBH_PATH_MAX, false), fpath)? {
        c.report_file = v;
    }
    if let Some(v) = optional(h::get_string(cfg, b, LOG, b"alert_file", fpath, RBH_PATH_MAX, false), fpath)? {
        c.alert_file = v;
    }
    if let Some(v) = optional(h::get_string(cfg, b, LOG, b"alert_mail", h::MAIL, 256, false), h::MAIL)? {
        c.alert_mail = v;
    }
    if cfg!(feature = "changelogs") {
        if let Some(v) = optional(h::get_string(cfg, b, LOG, b"changelogs_file", fpath, RBH_PATH_MAX, false), fpath)? {
            c.changelogs_file = v;
        }
    }
    let f = h::POSITIVE | h::NOT_NULL;
    if let Some(v) = optional(h::get_duration(cfg, b, LOG, b"stats_interval", f, false), f)? {
        c.stats_interval = v;
    }
    if let Some(v) = optional(h::get_int(cfg, b, LOG, b"batch_alert_max", h::POSITIVE, false), h::POSITIVE)? {
        c.batch_alert_max = v;
    }
    for (name, field) in [("alert_show_attrs", &mut c.alert_show_attrs), ("log_procname", &mut c.log_process),
                          ("log_hostname", &mut c.log_host)] {
        if let Some(v) = optional(h::get_bool(cfg, b, LOG, name.as_bytes(), 0, false), 0)? {
            *field = v;
        }
    }
    match h::get_string(cfg, b, LOG, b"debug_level", h::NO_WILDCARDS, 1024, false) {
        Ok(p) => {
            let lvl = str2debuglevel(&p.value);
            if lvl < 0 {
                return Err(CfgError::inval(cat!(b"Invalid value for Log::debug_level: '", p.value,
                    b"'. CRIT, MAJOR, EVENT, VERB, DEBUG or FULL expected")));
            }
            c.debug_level = lvl;
        }
        Err(e) if e.rc == ENOENT => {}
        Err(e) => return Err(e),
    }
    match h::get_string(cfg, b, LOG, b"syslog_facility", h::NO_WILDCARDS, 1024, false) {
        Ok(p) => {
            let (mut fac, mut prio) = (c.syslog_facility, c.syslog_priority);
            let r = syslog_facility(&p.value, &mut fac, &mut prio);
            c.syslog_facility = fac;
            c.syslog_priority = prio;
            if r.is_err() {
                return Err(CfgError::new(ENOENT, cat!(b"Invalid syslog channel '", p.value,
                    b"': expected syntax: <facility>[.<priority>]")));
            }
        }
        Err(e) if e.rc == ENOENT => {}
        Err(e) => return Err(e),
    }
    for w in h::unknown_parameters(cfg, b, LOG, &allowed) {
        logs::display(log, logs::LVL_CRIT, "Config Check", &w);
    }
    Ok(c)
}

impl LogConfig {
    pub fn dump(&self) -> Vec<u8> {
        let mut o = b"log".to_vec();
        i_field(&mut o, "debug_level", self.debug_level as i64);
        s_field(&mut o, "log_file", &self.log_file);
        s_field(&mut o, "report_file", &self.report_file);
        s_field(&mut o, "alert_mail", &self.alert_mail);
        s_field(&mut o, "alert_file", &self.alert_file);
        if cfg!(feature = "changelogs") {
            s_field(&mut o, "changelogs_file", &self.changelogs_file);
        }
        i_field(&mut o, "syslog_facility", self.syslog_facility as i64);
        i_field(&mut o, "syslog_priority", self.syslog_priority as i64);
        i_field(&mut o, "batch_alert_max", self.batch_alert_max as i64);
        i_field(&mut o, "stats_interval", self.stats_interval);
        i_field(&mut o, "alert_show_attrs", self.alert_show_attrs as i64);
        i_field(&mut o, "log_process", self.log_process as i64);
        i_field(&mut o, "log_host", self.log_host as i64);
        o.push(b'\n');
        o
    }
}

// --- db_update_params -------------------------------------------------------------

pub const UPDT_NEVER: i32 = 0;
pub const UPDT_ALWAYS: i32 = 1;
pub const UPDT_ON_EVENT: i32 = 2;
pub const UPDT_ON_EVENT_PERIODIC: i32 = 3;
pub const UPDT_PERIODIC: i32 = 4;

#[derive(Clone, Copy, Debug, Default)]
pub struct UpdtItem {
    pub when: i32,
    pub period_min: u32,
    pub period_max: u32,
}

#[derive(Clone, Debug)]
pub struct UpdtParams {
    pub md: UpdtItem,
    pub path: UpdtItem,
    pub fileclass: UpdtItem,
}

impl Default for UpdtParams {
    fn default() -> UpdtParams {
        UpdtParams {
            md: UpdtItem { when: UPDT_ALWAYS, ..Default::default() },
            path: if cfg!(feature = "fid") {
                UpdtItem { when: UPDT_ON_EVENT_PERIODIC, period_min: 0, period_max: 3600 }
            } else {
                UpdtItem::default()
            },
            fileclass: UpdtItem { when: UPDT_ALWAYS, ..Default::default() },
        }
    }
}

fn read_update_item(s: &[u8], options: &[Vec<u8>]) -> Result<UpdtItem, CfgError> {
    let mut item = UpdtItem::default();
    if eq_nocase(s, b"never") {
        item.when = UPDT_NEVER;
    } else if eq_nocase(s, b"always") {
        item.when = UPDT_ALWAYS;
    } else if eq_nocase(s, b"on_event") {
        item.when = UPDT_ON_EVENT;
    } else if eq_nocase(s, b"periodic") {
        item.when = UPDT_PERIODIC;
        if options.len() != 1 {
            return Err(CfgError::inval(b"1 argument is expected for periodic update parameter. E.g. periodic(30s);".to_vec()));
        }
        let t = cvt::str2duration(&options[0]) as i64;
        if t == -1 {
            return Err(CfgError::inval(b"Invalid value for periodic update parameter: duration expected. E.g. periodic(5min);".to_vec()));
        }
        item.period_min = t as u32;
        item.period_max = t as u32;
    } else if eq_nocase(s, b"on_event_periodic") {
        item.when = UPDT_ON_EVENT_PERIODIC;
        if options.len() != 2 {
            return Err(CfgError::inval(b"2 arguments are expected for on_event_periodic update parameter. E.g. on_event_periodic(1s,30s);".to_vec()));
        }
        let t1 = cvt::str2duration(&options[0]) as i64;
        let t2 = cvt::str2duration(&options[1]) as i64;
        if t1 == -1 || t2 == -1 {
            return Err(CfgError::inval(b"Invalid value for on_event_periodic update parameter: durations expected. E.g. on_event_periodic(1s,5min);".to_vec()));
        }
        item.period_min = t1 as u32;
        item.period_max = t2 as u32;
    } else {
        return Err(CfgError::inval(cat!(b"Invalid update parameter '", crate::lexer::cstr(s),
            b"' (expected: never, always, on_event, periodic(<interval>), on_event_periodic(<intvl1>,<intvl2>)")));
    }
    Ok(item)
}

const UPDT: &[u8] = b"db_update_params";

pub fn read_updt(cfg: &Config, log: &LogState) -> Result<UpdtParams, CfgError> {
    let mut c = UpdtParams::default();
    let mut allowed = vec!["md_update"];
    if cfg!(feature = "fid") {
        allowed.push("path_update");
    }
    allowed.push("fileclass_update");
    let b = match h::get_block(cfg, UPDT) {
        Ok(b) => b,
        Err(e) if e.rc == ENOENT => match h::get_block(cfg, b"db_update_policy") {
            Ok(b) => {
                logs::display(log, logs::LVL_CRIT, "UpdtParams",
                    b"WARNING: block name 'db_update_policy' is deprecated. Use 'db_update_params' instead");
                b
            }
            Err(e) if e.rc == ENOENT => return Ok(c),
            Err(e) => return Err(e),
        },
        // C checks only for ENOENT here: on any other error (a duplicate
        // block, or not a block) its block pointer is NULL, every parameter
        // then reads as absent, and the defaults stand, silently.
        Err(_) => return Ok(c),
    };
    let item = |name: &[u8]| -> Result<Option<UpdtItem>, CfgError> {
        match h::get_string(cfg, b, UPDT, name, h::NO_WILDCARDS, 1024, true) {
            Ok(p) => read_update_item(&p.value, &p.extra).map(Some),
            Err(e) if e.rc == ENOENT => Ok(None),
            Err(e) => Err(e),
        }
    };
    if let Some(v) = item(b"md_update")? {
        c.md = v;
    }
    if cfg!(feature = "fid") {
        if let Some(v) = item(b"path_update")? {
            c.path = v;
        }
    }
    if let Some(v) = item(b"fileclass_update")? {
        if v.when == UPDT_ON_EVENT || v.when == UPDT_ON_EVENT_PERIODIC {
            return Err(CfgError::inval(b"Parameter not supported for fileclass update: 'never', 'always' or 'periodic' expected".to_vec()));
        }
        c.fileclass = v;
    }
    for w in h::unknown_parameters(cfg, b, UPDT, &allowed) {
        logs::display(log, logs::LVL_CRIT, "Config Check", &w);
    }
    Ok(c)
}

impl UpdtParams {
    pub fn dump(&self) -> Vec<u8> {
        let f = |i: &UpdtItem| format!("{},{},{}", i.when, i.period_min, i.period_max);
        let mut o = format!("updt md={}", f(&self.md)).into_bytes();
        if cfg!(feature = "fid") {
            o.extend_from_slice(format!(" path={}", f(&self.path)).as_bytes());
        }
        o.extend_from_slice(format!(" fileclass={}\n", f(&self.fileclass)).as_bytes());
        o
    }
}

// --- ListManager -----------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct LmgrConfig {
    pub server: Vec<u8>,
    pub db: Vec<u8>,
    pub user: Vec<u8>,
    pub password: Vec<u8>,
    pub port: i32,
    pub socket: Vec<u8>,
    pub engine: Vec<u8>,
    pub tokudb_compression: Vec<u8>,
    pub commit_behavior: u32,
    pub connect_retry_min: i64,
    pub connect_retry_max: i64,
    pub acct: bool,
}

impl Default for LmgrConfig {
    fn default() -> LmgrConfig {
        LmgrConfig {
            server: b"localhost".to_vec(),
            db: Vec::new(),
            user: b"robinhood".to_vec(),
            password: Vec::new(),
            port: 0,
            socket: Vec::new(),
            engine: b"InnoDB".to_vec(),
            tokudb_compression: b"tokudb_uncompressed".to_vec(),
            commit_behavior: 1,
            connect_retry_min: 1,
            connect_retry_max: 30,
            acct: true,
        }
    }
}

const LMGR: &[u8] = b"ListManager";
const MYSQL: &[u8] = b"MySQL";

/// C atoi(): leading spaces, a sign, digits; 0 otherwise.
fn atoi(s: &[u8]) -> i32 {
    let c = std::ffi::CString::new(crate::lexer::cstr(s)).unwrap();
    // SAFETY: a valid C string.
    unsafe { libc::atoi(c.as_ptr()) }
}

fn strerror(errno: i32) -> Vec<u8> {
    // SAFETY: strerror returns a valid C string.
    unsafe { std::ffi::CStr::from_ptr(libc::strerror(errno)) }.to_bytes().to_vec()
}

pub fn read_lmgr(cfg: &Config, log: &LogState) -> Result<LmgrConfig, CfgError> {
    let mut c = LmgrConfig::default();
    let allowed = ["commit_behavior", "connect_retry_interval_min", "connect_retry_interval_max", "accounting",
                   "MySQL", "SQLite", "user_acct", "group_acct"];
    let b = h::get_block(cfg, LMGR)?;
    let f = h::POSITIVE | h::NOT_NULL;
    if let Some(v) = optional(h::get_duration(cfg, b, LMGR, b"connect_retry_interval_min", f, false), f)? {
        c.connect_retry_min = v;
    }
    if let Some(v) = optional(h::get_duration(cfg, b, LMGR, b"connect_retry_interval_max", f, false), f)? {
        c.connect_retry_max = v;
    }
    if let Some(v) = optional(h::get_bool(cfg, b, LMGR, b"accounting", 0, false), 0)? {
        c.acct = v;
    }
    match h::get_string(cfg, b, LMGR, b"commit_behavior", h::NO_WILDCARDS, 1024, true) {
        Ok(p) => {
            if eq_nocase(&p.value, b"autocommit") {
                c.commit_behavior = 0;
            } else if eq_nocase(&p.value, b"transaction") {
                c.commit_behavior = 1;
            } else if eq_nocase(&p.value, b"periodic") {
                if p.extra.len() != 1 {
                    return Err(CfgError::inval(b"A single argument is expected for periodic commit behavior. Eg: commit_behavior = periodic(1000)".to_vec()));
                }
                c.commit_behavior = atoi(&p.extra[0]) as u32;
                if c.commit_behavior == 0 {
                    return Err(CfgError::inval(b"The argument for \"ListManager::commit_behavior = periodic\" must be a positive integer. Eg: commit_behavior = periodic(1000)".to_vec()));
                }
            } else {
                return Err(CfgError::inval(cat!(b"Invalid commit behavior '", p.value,
                    b"' (expected: autocommit, transaction, periodic(<count>))")));
            }
        }
        Err(e) if e.rc == ENOENT => {}
        Err(e) => return Err(e),
    }
    for name in ["user_acct", "group_acct"] {
        if let Ok(p) = h::get_bool(cfg, b, LMGR, name.as_bytes(), 0, false) {
            logs::display(log, logs::LVL_CRIT, "LmgrConfig", &cat!(b"WARNING: parameter ListManager::", name,
                b"' is deprecated. Specify 'accounting = yes/no' instead."));
            logs::display(log, logs::LVL_MAJOR, "LmgrConfig", &cat!(b"Setting 'accounting = ",
                if p.value { "yes" } else { "no" }, b"' for compatibility."));
            c.acct = p.value;
        }
    }
    for w in h::unknown_parameters(cfg, b, LMGR, &allowed) {
        logs::display(log, logs::LVL_CRIT, "Config Check", &w);
    }

    let db = h::get_block(cfg, b"ListManager::MySQL")?;
    let nw = h::NO_WILDCARDS;
    if let Some(v) = optional(h::get_string(cfg, db, MYSQL, b"server", nw, 256, false), nw)? {
        c.server = v;
    }
    let f = h::MANDATORY | nw;
    if let Some(v) = optional(h::get_string(cfg, db, MYSQL, b"db", f, 256, false), f)? {
        c.db = v;
    }
    if let Some(v) = optional(h::get_string(cfg, db, MYSQL, b"user", nw, 256, false), nw)? {
        c.user = v;
    }
    let f = h::POSITIVE | h::NOT_NULL;
    if let Some(v) = optional(h::get_int(cfg, db, MYSQL, b"port", f, false), f)? {
        c.port = v;
    }
    let f = nw | h::ABSOLUTE_PATH;
    if let Some(v) = optional(h::get_string(cfg, db, MYSQL, b"socket", f, RBH_PATH_MAX, false), f)? {
        c.socket = v;
    }
    let f = nw | h::NOT_EMPTY;
    if let Some(v) = optional(h::get_string(cfg, db, MYSQL, b"engine", f, 1024, false), f)? {
        c.engine = v;
    }
    if let Some(v) = optional(h::get_string(cfg, db, MYSQL, b"tokudb_compression", nw, 50, false), nw)? {
        c.tokudb_compression = v;
    }
    match h::get_string(cfg, db, MYSQL, b"password", 0, 256, false) {
        Ok(p) => c.password = p.value,
        Err(e) if e.rc != ENOENT => return Err(e),
        Err(_) => {
            let path = match h::get_string(cfg, db, MYSQL, b"password_file", h::ABSOLUTE_PATH | nw, 1024, false) {
                Ok(p) => p.value,
                Err(e) if e.rc == ENOENT => {
                    return Err(CfgError::new(ENOENT, b"MySQL::password or MySQL::password_file must be provided".to_vec()));
                }
                Err(e) => return Err(e),
            };
            match read_password_file(&path)? {
                Some(pw) => c.password = pw,
                // C: a read error with errno 0 returns 0 from the reader at
                // once: no error, no password, no unknown-parameter check
                None => return Ok(c),
            }
        }
    }
    for w in h::unknown_parameters(cfg, db, MYSQL, &["server", "db", "user", "password", "password_file", "port",
                                                     "socket", "engine", "tokudb_compression"]) {
        logs::display(log, logs::LVL_CRIT, "Config Check", &w);
    }
    Ok(c)
}

/// The password file's first word, as fopen + fscanf("%1023s") read it.
fn read_password_file(path: &[u8]) -> Result<Option<Vec<u8>>, CfgError> {
    let cpath = std::ffi::CString::new(path.to_vec()).unwrap();
    // SAFETY: C stdio on a valid path; the buffer holds 1023 bytes and the NUL.
    unsafe {
        let f = libc::fopen(cpath.as_ptr(), b"r\0".as_ptr() as *const libc::c_char);
        if f.is_null() {
            let errno = *libc::__errno_location();
            return Err(CfgError::new(errno, cat!(b"Error opening password file ", path, b" : ", strerror(errno))));
        }
        let mut buf = vec![0u8; 1024];
        buf[..path.len().min(1023)].copy_from_slice(&path[..path.len().min(1023)]);
        let rc = libc::fscanf(f, b"%1023s\0".as_ptr() as *const libc::c_char, buf.as_mut_ptr());
        if libc::ferror(f) != 0 || rc < 1 {
            let errno = *libc::__errno_location();
            // C uses the GNU strerror_r, which returns a pointer: its
            // `if (strerror_r(...))` is always true, so the number is printed.
            let e = errno.to_string().into_bytes();
            if errno == 0 {
                return Ok(None);
            }
            return Err(CfgError::new(errno, cat!(b"Error reading password file ", crate::lexer::cstr(&buf), b" : ", e)));
        }
        libc::fclose(f);
        Ok(Some(crate::lexer::strncpy(&buf, 256)))
    }
}

impl LmgrConfig {
    pub fn dump(&self) -> Vec<u8> {
        let mut o = b"lmgr".to_vec();
        s_field(&mut o, "server", &self.server);
        s_field(&mut o, "db", &self.db);
        s_field(&mut o, "user", &self.user);
        s_field(&mut o, "password", &self.password);
        i_field(&mut o, "port", self.port as i64);
        s_field(&mut o, "socket", &self.socket);
        s_field(&mut o, "engine", &self.engine);
        s_field(&mut o, "tokudb_compression", &self.tokudb_compression);
        i_field(&mut o, "commit_behavior", self.commit_behavior as i64);
        i_field(&mut o, "connect_retry_min", self.connect_retry_min);
        i_field(&mut o, "connect_retry_max", self.connect_retry_max);
        i_field(&mut o, "acct", self.acct as i64);
        o.push(b'\n');
        o
    }
}

#[allow(dead_code)]
fn _unused(_: &Item) -> i32 {
    EINVAL
}
