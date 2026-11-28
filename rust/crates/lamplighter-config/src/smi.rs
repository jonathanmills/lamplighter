// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// Translated from Robinhood's src/common/rbh_modules.c,
// src/policies/status_manager.c and the module descriptors in
// src/modules/ (Copyright (C) CEA/DAM, CeCILL-C).

//! Engine modules and status manager instances.
//!
//! The C tools dlopen `librbh_mod_<name>.so` to find a module's status
//! manager and actions. The Rust tools know the modules of their build
//! from built-in tables (decided 2026-11-28, doc/RUST-PLAN.md), but
//! still look for the module file as the dynamic loader would, so that a
//! module that is not installed fails as it does in C, with the same
//! messages (dlerror's text included).
//!
//! A module file that is installed but unknown to these tables (a
//! third-party module) is the one intended difference: the C tools load
//! it, the Rust tools report it as unsupported.

use crate::cat;
use crate::cvt::eq_nocase;
use crate::logs::{self, LogState, LVL_CRIT, LVL_DEBUG};

/// cfg_param_type: how a status manager attribute is read in criteria.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CritType {
    String,
    Size,
    Int,
    Int64,
    Float,
    Bool,
    Duration,
    Type,
}

/// sm_info_def_t (user name, short DB name, criteria type)
#[derive(Debug)]
pub struct InfoDef {
    pub user_name: &'static str,
    pub db_name: &'static str,
    pub crit_type: CritType,
}

pub const SM_SHARED: u32 = 1 << 0;
pub const SM_DELETED: u32 = 1 << 1;
pub const SM_MULTI_ACTION: u32 = 1 << 2;

/// status_manager_t, as far as configuration needs it.
#[derive(Debug)]
pub struct StatusManager {
    pub name: &'static str,
    pub flags: u32,
    pub statuses: &'static [&'static str],
    pub info: &'static [InfoDef],
    /// check_action_name (strcasecmp against these), or None
    pub actions: Option<&'static [&'static str]>,
}

/// A module: what its library exports.
#[derive(Debug)]
pub struct Module {
    /// the library name: librbh_mod_<file>.so
    pub file: &'static str,
    /// mod_get_name() (None: the symbol is missing and loading fails)
    pub name: Option<&'static str>,
    pub sm: Option<&'static StatusManager>,
    /// mod_get_action exists; these names (strcmp) return a function
    pub actions: Option<&'static [&'static str]>,
    /// mod_get_scheduler exists; these names return a scheduler
    pub schedulers: Option<&'static [&'static str]>,
}

const fn info(user_name: &'static str, db_name: &'static str, crit_type: CritType) -> InfoDef {
    InfoDef { user_name, db_name, crit_type }
}

// --- the modules (src/modules/*.c) ---------------------------------------------

static BASIC_SM: StatusManager = StatusManager {
    name: "basic", flags: 0, statuses: &["ok", "failed"], info: &[], actions: None,
};

static ALERTER_SM: StatusManager = StatusManager {
    name: "alerter", flags: 0, statuses: &["clear", "alert"],
    info: &[info("last_check", "lstchk", CritType::Duration), info("last_alert", "lstalrt", CritType::Duration)],
    actions: None,
};

static CHECKER_SM: StatusManager = StatusManager {
    name: "checker", flags: 0, statuses: &["ok", "failed"],
    info: &[info("last_check", "lstchk", CritType::Duration), info("last_success", "lstsuc", CritType::Duration),
            info("output", "out", CritType::String)],
    actions: None,
};

static MODEGUARD_SM: StatusManager = StatusManager {
    name: "modeguard", flags: 0, statuses: &["ok", "invalid"], info: &[], actions: None,
};

#[cfg(feature = "lhsm")]
static LHSM_SM: StatusManager = StatusManager {
    name: "lhsm", flags: SM_SHARED | SM_DELETED | SM_MULTI_ACTION,
    statuses: &["new", "modified", "retrieving", "archiving", "synchro", "released"],
    info: &[info("archive_id", "archid", CritType::Int), info("no_release", "norels", CritType::Bool),
            info("no_archive", "noarch", CritType::Bool), info("last_archive", "lstarc", CritType::Duration),
            info("last_restore", "lstrst", CritType::Duration), info("uuid", "uuid", CritType::String)],
    actions: Some(&["archive", "release", "removed", "deleted"]),
};

/// backup.c, built as librbh_mod_backup (HSM_LITE) and librbh_mod_shook
#[allow(unused_macros)]
macro_rules! backup_sm {
    ($name:expr, $actions:expr) => {
        StatusManager {
            name: $name, flags: SM_SHARED | SM_DELETED | SM_MULTI_ACTION,
            statuses: &["new", "modified", "retrieving", "archiving", "synchro", "released", "release_pending"],
            info: &[info("backend_path", "bkpath", CritType::String), info("last_archive", "lstarc", CritType::Duration)],
            actions: Some($actions),
        }
    };
}

#[cfg(all(feature = "lustre", feature = "fid"))]
static BACKUP_SM: StatusManager = backup_sm!("backup", &["archive", "removed", "deleted"]);

#[cfg(feature = "shook")]
static SHOOK_SM: StatusManager = backup_sm!("shook", &["archive", "release", "removed", "deleted"]);

#[cfg(feature = "shook_lhsmify")]
const SHOOK_ACTIONS: &[&str] = &["shook.release", "shook.recover", "shook.release_lhsmify", "shook.lhsmify"];
#[cfg(all(feature = "shook", not(feature = "shook_lhsmify")))]
const SHOOK_ACTIONS: &[&str] = &["shook.release", "shook.recover"];

/// The modules this build installs (src/modules/Makefile.am).
pub static MODULES: &[Module] = &[
    Module {
        file: "common", name: Some("common"), sm: None,
        actions: Some(&["common.unlink", "common.rmdir", "common.log", "common.copy", "common.sendfile",
                        "common.gzip", "common.move"]),
        schedulers: Some(&["common.max_per_run", "common.rate_limit"]),
    },
    Module { file: "alerter", name: Some("alerter"), sm: Some(&ALERTER_SM), actions: Some(&["alerter.alert"]), schedulers: None },
    Module { file: "checker", name: Some("checker"), sm: Some(&CHECKER_SM), actions: Some(&[]), schedulers: None },
    Module { file: "basic", name: Some("basic"), sm: Some(&BASIC_SM), actions: None, schedulers: None },
    Module {
        file: "modeguard", name: Some("modeguard"), sm: Some(&MODEGUARD_SM),
        actions: Some(&["modeguard.enforce_mode"]), schedulers: None,
    },
    // test_sched.c has no mod_get_name: this module never loads
    Module { file: "test", name: None, sm: None, actions: None, schedulers: Some(&["test.sleeper"]) },
    #[cfg(feature = "lhsm")]
    Module {
        file: "lhsm", name: Some("lhsm"), sm: Some(&LHSM_SM),
        actions: Some(&["lhsm.archive", "lhsm.release", "lhsm.hsm_remove", "lhsm.remove"]), schedulers: None,
    },
    #[cfg(all(feature = "lustre", feature = "fid"))]
    Module { file: "backup", name: Some("backup"), sm: Some(&BACKUP_SM), actions: Some(&[]), schedulers: None },
    #[cfg(feature = "shook")]
    Module { file: "shook", name: Some("shook"), sm: Some(&SHOOK_SM), actions: Some(SHOOK_ACTIONS), schedulers: None },
];

// --- finding module files as the dynamic loader does ----------------------------

/// MAX_MOD_NAMELEN
const MAX_MOD_NAMELEN: usize = 128;
const MODULE_TAG: &str = "mod_mgt";

/// module_fullname_build: "librbh_mod_<name>.so", lower case, as snprintf
/// with MAX_MOD_NAMELEN - 1 writes it; None when it is too long.
fn fullname(name: &[u8]) -> Option<Vec<u8>> {
    let full = cat!(b"librbh_mod_", name, b".so");
    if full.len() >= MAX_MOD_NAMELEN {
        return None;
    }
    let mut out = full;
    out.truncate(MAX_MOD_NAMELEN - 2);
    // tolower in the C locale
    Some(out.iter().map(|c| c.to_ascii_lowercase()).collect())
}

/// The directories ld.so searches for a name without '/': LD_LIBRARY_PATH,
/// the executable's RUNPATH (the engine's module directory), the system
/// directories.
fn search_dirs() -> Vec<Vec<u8>> {
    use std::os::unix::ffi::OsStrExt;
    let mut dirs = Vec::new();
    if let Some(p) = std::env::var_os("LD_LIBRARY_PATH") {
        for d in p.as_bytes().split(|&c| c == b':' || c == b';') {
            let mut d = if d.is_empty() { b".".to_vec() } else { d.to_vec() };
            if d.len() > 1 && d.last() == Some(&b'/') {
                d.pop();
            }
            dirs.push(d);
        }
    }
    if let Some(runpath) = option_env!("LAMPLIGHTER_PKGLIBDIR") {
        if !runpath.is_empty() {
            dirs.push(runpath.as_bytes().to_vec());
        }
    }
    for d in ["/lib64", "/usr/lib64"] {
        dirs.push(d.as_bytes().to_vec());
    }
    dirs
}

/// Where dlopen() would find this file (its l_name), or None.
fn locate(file: &[u8]) -> Option<Vec<u8>> {
    use std::os::unix::ffi::OsStrExt;
    let exists = |p: &[u8]| std::path::Path::new(std::ffi::OsStr::from_bytes(p)).is_file();
    if file.contains(&b'/') {
        return if exists(file) { Some(file.to_vec()) } else { None };
    }
    search_dirs().into_iter().map(|d| cat!(d, b"/", file)).find(|p| exists(p))
}

// --- status manager instances ------------------------------------------------------

/// sm_instance_t
#[derive(Debug)]
pub struct Smi {
    pub instance_name: Vec<u8>,
    pub db_field: Vec<u8>,
    pub user_name: Vec<u8>,
    pub sm: &'static StatusManager,
    pub smi_index: u32,
    pub sm_info_offset: u32,
}

/// struct _sm_attr_info
#[derive(Debug)]
pub struct SmAttrInfo {
    pub db_attr_name: Vec<u8>,
    pub user_attr_name: Vec<u8>,
    pub def: &'static InfoDef,
    pub smi: u32,
}

/// The process-wide state of rbh_modules.c and status_manager.c: loaded
/// modules, status manager instances, their attributes.
#[derive(Debug, Default)]
pub struct Registry {
    loaded: Vec<&'static Module>,
    pub instances: Vec<Smi>,
    pub attrs: Vec<SmAttrInfo>,
}

/// The attribute a name designates (sm_attr_get with no attribute set).
pub enum SmAttr {
    Status { smi: u32 },
    Info { smi: u32, def: &'static InfoDef, index: u32 },
}

impl SmAttr {
    /// The attribute index (ATTR_INDEX_FLG_STATUS or _SMINFO, plus the index)
    pub fn index(&self) -> u32 {
        match self {
            SmAttr::Status { smi } => crate::attrs::ATTR_INDEX_FLG_STATUS | smi,
            SmAttr::Info { index, .. } => crate::attrs::ATTR_INDEX_FLG_SMINFO | index,
        }
    }
    pub fn crit_type(&self) -> CritType {
        match self {
            SmAttr::Status { .. } => CritType::String,
            SmAttr::Info { def, .. } => def.crit_type,
        }
    }
}

impl Registry {
    /// module_load: find the file, "dlopen" it, check its symbols.
    fn load(&mut self, log: &LogState, name: &[u8]) -> Option<&'static Module> {
        let file = fullname(name)?;
        let Some(path) = locate(&file) else {
            logs::display(log, LVL_CRIT, MODULE_TAG, &cat!(b"Cannot dlopen() '", file, b"': ", file,
                b": cannot open shared object file: No such file or directory"));
            return None;
        };
        let Some(m) = MODULES.iter().find(|m| fullname(m.file.as_bytes()).as_deref() == Some(&file[..])) else {
            logs::display(log, LVL_CRIT, MODULE_TAG, &cat!(b"Cannot dlopen() '", file, b"': ", path,
                b": this Lamplighter tool does not support modules from other sources"));
            return None;
        };
        let missing = |sym: &str| cat!(path, b": undefined symbol: ", sym);
        if m.name.is_none() {
            logs::display(log, LVL_CRIT, MODULE_TAG, &cat!(b"Cannot load mod_get_name from module ", file, b": ",
                missing("mod_get_name")));
            return None;
        }
        for (sym, present) in [("mod_get_status_manager", m.sm.is_some()), ("mod_get_action", m.actions.is_some()),
                               ("mod_get_scheduler", m.schedulers.is_some())] {
            if !present {
                logs::display(log, LVL_DEBUG, MODULE_TAG, &cat!(b"Module '", file, b"': optional symbol '", sym,
                    b"' not found: ", missing(sym)));
            }
        }
        logs::display(log, LVL_DEBUG, MODULE_TAG, &cat!(b"Successfully loaded module '", m.name.unwrap(), b"'"));
        self.loaded.insert(0, m);
        Some(m)
    }

    /// module_get: a loaded module (by its name), else load it.
    fn module_get(&mut self, log: &LogState, name: &[u8]) -> Option<&'static Module> {
        let name = crate::lexer::cstr(name);
        if let Some(m) = self.loaded.iter().find(|m| eq_nocase(m.name.unwrap().as_bytes(), name)) {
            return Some(m);
        }
        self.load(log, name)
    }

    /// module_get_action: is "<module>.<action>" a known function?
    pub fn action_exists(&mut self, log: &LogState, name: &[u8]) -> bool {
        let Some(dot) = name.iter().position(|&c| c == b'.') else { return false };
        match self.module_get(log, &name[..dot]) {
            Some(Module { actions: Some(a), .. }) => a.iter().any(|x| x.as_bytes() == name),
            _ => false,
        }
    }

    /// create_sm_instance: the instance index, or None when the status
    /// manager cannot be loaded or does not fit.
    pub fn create_instance(&mut self, log: &LogState, pol_name: &[u8], sm_name: &[u8]) -> Option<u32> {
        let sm = self.module_get(log, sm_name)?.sm?;
        let shared = sm.flags & SM_SHARED != 0;
        if let Some(i) = self.instances.iter().find(|i| {
            eq_nocase(i.sm.name.as_bytes(), sm_name) && (shared || eq_nocase(&i.instance_name, pol_name))
        }) {
            return Some(i.smi_index);
        }
        let count = self.instances.len() as u32;
        let instance_name = if shared { sm.name.as_bytes().to_vec() } else { pol_name.to_vec() };
        if count + 1 >= 32 {
            logs::display(log, LVL_CRIT, "smi_create", b"Too many status managers: max 32 supported");
            return None;
        }
        if self.attrs.len() + sm.info.len() >= 64 {
            logs::display(log, LVL_CRIT, "smi_create", b"Too many policy-specific attributes: max 64 supported");
            return None;
        }
        let offset = if count == 0 { 0 } else { self.attrs.len() as u32 };
        for def in sm.info {
            self.attrs.push(SmAttrInfo {
                db_attr_name: cat!(instance_name, b"_", def.db_name),
                user_attr_name: cat!(instance_name, b".", def.user_name),
                def,
                smi: count,
            });
        }
        self.instances.push(Smi {
            db_field: cat!(instance_name, b"_status"),
            user_name: cat!(instance_name, b".status"),
            instance_name,
            sm,
            smi_index: count,
            sm_info_offset: offset,
        });
        Some(count)
    }

    pub fn smi(&self, index: u32) -> &Smi {
        &self.instances[index as usize]
    }

    /// smi_by_name (strcmp)
    fn by_name(&self, name: &[u8]) -> Option<&Smi> {
        self.instances.iter().find(|i| i.instance_name == name)
    }

    /// sm_attr_get(smi, NULL, name, ...): the attribute a criteria or sort
    /// attribute name designates, in the context of an instance (or none).
    /// Logs like C when "<instance>." names an unknown instance.
    pub fn attr_get(&self, log: &LogState, smi: Option<u32>, name: &[u8]) -> Option<SmAttr> {
        let name = crate::lexer::cstr(name);
        let (smi, attr) = match name.iter().position(|&c| c == b'.') {
            None => (self.smi(smi?), name),
            Some(dot) => match self.by_name(&name[..dot]) {
                Some(s) => (s, &name[dot + 1..]),
                None => {
                    logs::display(log, LVL_CRIT, "sm_attr_get", &cat!(b"ERROR: unknown status manager '", &name[..dot],
                        b"' in parameter '", name, b"'"));
                    return None;
                }
            },
        };
        if eq_nocase(attr, b"status") {
            return Some(SmAttr::Status { smi: smi.smi_index });
        }
        smi.sm.info.iter().enumerate().find(|(_, d)| eq_nocase(attr, d.user_name.as_bytes())).map(|(i, def)| {
            SmAttr::Info { smi: smi.smi_index, def, index: smi.sm_info_offset + i as u32 }
        })
    }
}

impl StatusManager {
    /// get_status_str (strcmp; never an empty string)
    pub fn status(&self, s: &[u8]) -> Option<&'static str> {
        if s.is_empty() {
            return None;
        }
        self.statuses.iter().copied().find(|x| x.as_bytes() == s)
    }

    /// allowed_status_str
    pub fn allowed(&self) -> Vec<u8> {
        let mut out = b"\"\"(empty)".to_vec();
        for s in self.statuses {
            out.push(b',');
            out.extend_from_slice(s.as_bytes());
        }
        out
    }

    /// smi_support_action (check_action_name)
    pub fn supports_action(&self, name: &[u8]) -> bool {
        self.actions.map_or(false, |a| a.iter().any(|x| eq_nocase(x.as_bytes(), name)))
    }
}
