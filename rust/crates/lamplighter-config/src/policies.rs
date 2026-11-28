// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// Translated from Robinhood's src/policies/policy_loader.c and the
// placeholder parsing of src/common/param_utils.c (Copyright (C) CEA/DAM,
// CeCILL-C).

//! The policies module: policy definitions (`define_policy` blocks),
//! with their actions, status managers, sort attributes and scopes.
//! Fileclasses and policy rules follow in stage 2a.4.

use crate::access::{self, Config, Kind};
use crate::attrs::{AttrMask, ATTR_INDEX_FULLPATH, ATTR_INDEX_NAME, ATTR_INDEX_PARENT_ID, ATTR_INDEX_STRIPE_INFO};
use crate::boolexpr::{self, Ctx, Node, LRU_ATTR_INVAL};
use crate::cat;
use crate::cvt::eq_nocase;
use crate::helpers::{self as h, CfgError, EEXIST, ENOENT};
use crate::logs::{self, LogState, LVL_CRIT, LVL_MAJOR};
use crate::smi::Registry;
use crate::tree::Item;

const POLICY_DECLARATION: &[u8] = b"define_policy";
const POLICY_NAME_LEN: usize = 128;
const LOADER_TAG: &str = "PolicyLoader";

/// sort_order_t
pub const SORT_ASC: i32 = 1;
pub const SORT_DESC: i32 = 2;

/// policy_action_t
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    Unset,
    None,
    Function(Vec<u8>),
    Command(Vec<Vec<u8>>),
}

impl Action {
    fn type_value(&self) -> i32 {
        match self {
            Action::Unset => 0,
            Action::None => 1,
            Action::Function(_) => 2,
            Action::Command(_) => 3,
        }
    }
}

/// policy_descr_t, as far as the definitions fill it.
#[derive(Clone, Debug)]
pub struct PolicyDescr {
    pub name: Vec<u8>,
    pub scope: Node,
    pub scope_mask: AttrMask,
    pub implements: Option<Vec<u8>>,
    pub status_current: Option<&'static str>,
    pub status_mgr: Option<u32>,
    pub default_action: Action,
    pub default_lru_sort_attr: u32,
    pub default_lru_sort_order: i32,
    /// rules.run_attr_mask
    pub run_attr_mask: AttrMask,
    pub manage_deleted: bool,
}

/// policies_t
#[derive(Clone, Debug, Default)]
pub struct Policies {
    pub policy_list: Vec<PolicyDescr>,
    pub manage_deleted: bool,
}

// --- cmd() actions ---------------------------------------------------------------

/// The helpers Robinhood shipped under other names (renamed_cmds[] in
/// policy_loader.c; kept in sync with RENAMED_CMDS in lamplighter-config).
const RENAMED_CMDS: &[(&str, &str)] = &[
    ("robinhood", "lamplighter"),
    ("rbh-report", "lamplighter-report"),
    ("rbh-find", "lux-find"),
    ("rbh-du", "lux-du"),
    ("rbh-diff", "lamplighter-diff"),
    ("rbh-undelete", "lamplighter-undelete"),
    ("rbh-rebind", "lamplighter-rebind"),
    ("rbh-config", "lamplighter-config"),
    ("rbh_cksum.sh", "lamplighter-cksum.sh"),
    ("rbhext_tool", "lamplighter-ext-tool"),
    ("rbhext_tool_svr", "lamplighter-ext-tool-svr"),
    ("rbhext_tool_clnt", "lamplighter-ext-tool-clnt"),
];

fn executable(path: &[u8]) -> bool {
    let Ok(p) = std::ffi::CString::new(path) else { return false };
    // SAFETY: a valid C string.
    unsafe { libc::access(p.as_ptr(), libc::X_OK) == 0 }
}

/// cmd_installed: a path is checked as is, a name in $PATH.
fn cmd_installed(prog: &[u8]) -> bool {
    use std::os::unix::ffi::OsStrExt;
    if prog.contains(&b'/') {
        return executable(prog);
    }
    let Some(path) = std::env::var_os("PATH") else { return false };
    path.as_bytes().split(|&c| c == b':').filter(|d| !d.is_empty()).any(|d| {
        executable(&crate::lexer::strncpy(&cat!(d, b"/", prog), 4096))
    })
}

/// translate_renamed_cmd
fn translate_renamed_cmd(log: &LogState, argv: &mut [Vec<u8>], param: &[u8], cmd: &[u8]) {
    let Some(arg0) = argv.first() else { return };
    let start = arg0.iter().rposition(|&c| c == b'/').map_or(0, |i| i + 1);
    let base = &arg0[start..];
    let Some((_, new_name)) = RENAMED_CMDS.iter().find(|(old, _)| old.as_bytes() == base) else { return };
    if cmd_installed(arg0) {
        logs::display(log, LVL_MAJOR, LOADER_TAG, &cat!(b"WARNING: ", param, b" = cmd(\"", cmd, b"\"): '", base,
            b"' is Robinhood's name for Lamplighter's '", new_name,
            b"'. It runs as written, because the old name is installed (lamplighter-compat). Please update the configuration: see 'lamplighter-config check'."));
        return;
    }
    let new_prog = cat!(&arg0[..start], new_name);
    logs::display(log, LVL_MAJOR, LOADER_TAG, &cat!(b"WARNING: ", param, b" = cmd(\"", cmd, b"\"): '", arg0,
        b"' is Robinhood's name for Lamplighter's '", new_name, b"' and is not installed, so '", new_prog,
        b"' runs instead. Please update the configuration: see 'lamplighter-config check'."));
    argv[0] = new_prog;
}

/// g_shell_parse_argv, from GLib itself (as the C code calls it)
mod glib {
    use std::os::raw::{c_char, c_int};

    #[repr(C)]
    struct GError {
        domain: u32,
        code: c_int,
        message: *mut c_char,
    }

    #[link(name = "glib-2.0")]
    extern "C" {
        fn g_shell_parse_argv(cmd: *const c_char, argcp: *mut c_int, argvp: *mut *mut *mut c_char,
                              error: *mut *mut GError) -> c_int;
        fn g_strfreev(v: *mut *mut c_char);
        fn g_error_free(e: *mut GError);
    }

    /// The words, or GLib's error message.
    pub fn shell_parse_argv(cmd: &[u8]) -> Result<Vec<Vec<u8>>, Vec<u8>> {
        let c = std::ffi::CString::new(crate::lexer::cstr(cmd)).expect("no NUL");
        let mut argv: *mut *mut c_char = std::ptr::null_mut();
        let mut err: *mut GError = std::ptr::null_mut();
        // SAFETY: GLib's documented contract: on success argv is a
        // NULL-terminated vector we free, on failure err is set.
        unsafe {
            if g_shell_parse_argv(c.as_ptr(), std::ptr::null_mut(), &mut argv, &mut err) == 0 {
                let msg = std::ffi::CStr::from_ptr((*err).message).to_bytes().to_vec();
                g_error_free(err);
                return Err(msg);
            }
            let mut out = Vec::new();
            let mut p = argv;
            while !(*p).is_null() {
                out.push(std::ffi::CStr::from_ptr(*p).to_bytes().to_vec());
                p = p.add(1);
            }
            g_strfreev(argv);
            Ok(out)
        }
    }
}

/// match_varname
fn match_varname(s: &[u8]) -> bool {
    if s.is_empty() {
        return true;
    }
    if !s[0].is_ascii_alphabetic() {
        return false;
    }
    s[1..].iter().all(|&c| c.is_ascii_alphanumeric() || c == b'_' || c == b'.')
}

/// params_mask: the attributes the {placeholders} of a string need, or
/// None after logging an error (an empty placeholder).
fn params_mask(log: &LogState, s: &[u8], descr: &[u8]) -> Option<AttrMask> {
    let mut mask = AttrMask::NULL;
    let mut pass = 0;
    loop {
        let Some(begin) = s[pass..].iter().position(|&c| c == b'{').map(|i| i + pass) else { break };
        let Some(end) = s[begin..].iter().position(|&c| c == b'}').map(|i| i + begin) else { break };
        if end == begin + 1 {
            logs::display(log, LVL_CRIT, "params", &cat!(b"ERROR: empty var name in ", descr));
            return None;
        }
        let name = &s[begin + 1..end];
        if !match_varname(name) {
            pass = begin + 1;
            continue;
        }
        let index = [("name", Some(ATTR_INDEX_NAME)), ("path", Some(ATTR_INDEX_FULLPATH)),
                     ("fullpath", Some(ATTR_INDEX_FULLPATH)), ("fid", None), ("parent_fid", Some(ATTR_INDEX_PARENT_ID)),
                     ("ost_pool", Some(ATTR_INDEX_STRIPE_INFO)), ("fsname", None), ("fsroot", None), ("fspath", None),
                     ("cfg", None)]
            .iter().find(|(n, _)| eq_nocase(n.as_bytes(), name)).and_then(|(_, i)| *i);
        if let Some(i) = index {
            mask.set_index(i);
        }
        pass = end + 1;
    }
    Some(mask)
}

/// parse_policy_action
pub fn parse_policy_action(reg: &mut Registry, log: &LogState, name: &[u8], value: &[u8], extra: &[Vec<u8>],
                           mask: &mut AttrMask) -> Result<Action, CfgError> {
    if eq_nocase(value, b"none") {
        if !extra.is_empty() {
            return Err(CfgError::inval(cat!(b"No extra argument is expected for '", name, b" = ", value, b"'")));
        }
        return Ok(Action::None);
    }
    if eq_nocase(value, b"cmd") {
        if extra.len() != 1 {
            return Err(CfgError::inval(cat!(b"A single argument is expected for cmd. E.g.: ", name,
                b" = cmd(\"myscript.sh\");")));
        }
        let mut argv = glib::shell_parse_argv(&extra[0]).map_err(|m| {
            CfgError::inval(cat!(b"Could not parse command ", crate::lexer::cstr(&extra[0]), b": ", m, b"\n"))
        })?;
        translate_renamed_cmd(log, &mut argv, name, crate::lexer::cstr(&extra[0]));
        for arg in &argv {
            let Some(m) = params_mask(log, arg, name) else {
                return Err(CfgError::inval(cat!(b"Unexpected parameters in ", name, b" cmd")));
            };
            *mask = mask.or(&m);
        }
        return Ok(Action::Command(argv));
    }
    if !extra.is_empty() {
        return Err(CfgError::inval(cat!(b"No extra argument is expected for '", name, b" = ", value, b"'")));
    }
    if !reg.action_exists(log, value) {
        return Err(CfgError::inval(cat!(name, b": unknown function '", value, b"'")));
    }
    Ok(Action::Function(value.to_vec()))
}

fn lower(s: &[u8]) -> Vec<u8> {
    s.iter().map(|c| c.to_ascii_lowercase()).collect()
}

/// The context the policy definitions read in.
pub struct Env<'a> {
    pub log: &'a LogState,
    pub uid_gid_as_numbers: bool,
}

/// parse_policy_decl
fn parse_policy_decl(cfg: &Config, blk: &Item, block_name: &[u8], pols: &Policies, reg: &mut Registry, env: &Env)
    -> Result<PolicyDescr, CfgError> {
    let log = env.log;
    let name = crate::lexer::cstr(access::block_id(blk).unwrap_or(b""));
    if pols.policy_list.iter().any(|p| eq_nocase(&p.name, name)) {
        return Err(CfgError::inval(cat!(b"Duplicate definition of policy '", name, b"'.")));
    }
    if name.len() > POLICY_NAME_LEN - 1 {
        return Err(CfgError::inval(format!("Policy name is too long (max: {}).", POLICY_NAME_LEN - 1).into_bytes()));
    }
    let mut p = PolicyDescr {
        name: name.to_vec(),
        scope: Node::Constant(false),
        scope_mask: AttrMask::NULL,
        implements: None,
        status_current: None,
        status_mgr: None,
        default_action: Action::Unset,
        default_lru_sort_attr: 0,
        default_lru_sort_order: 0,
        run_attr_mask: AttrMask::NULL,
        manage_deleted: false,
    };
    let blk_and_name = cat!(block_name, b" ", name);

    let a = h::get_string(cfg, blk, block_name, b"default_action", h::MANDATORY, 1024, true)?;
    p.default_action = parse_policy_action(reg, log, b"default_action", &a.value, &a.extra, &mut p.run_attr_mask)?;

    let sm = match h::get_string(cfg, blk, block_name, b"status_manager", h::MANDATORY | h::NO_WILDCARDS, 1024, true) {
        Ok(v) => v,
        Err(mut e) => {
            if e.rc == ENOENT {
                e.msg.extend_from_slice(b"\nIf you don't need a status manager, you should explicitely specify: status_manager=none");
            }
            return Err(e);
        }
    };
    if eq_nocase(&sm.value, b"none") {
        if let Some(x) = sm.extra.first() {
            return Err(CfgError::inval(cat!(b"No argument expected after 'status_manager = none': found '", x, b"'")));
        }
    } else {
        let Some(smi) = reg.create_instance(log, &p.name, &sm.value) else {
            return Err(CfgError::inval(cat!(b"Could not load status manager '", sm.value, b"'")));
        };
        p.status_mgr = Some(smi);
        let sm_def = reg.smi(smi).sm;
        match sm.extra.len() {
            0 => {
                if sm_def.flags & crate::smi::SM_MULTI_ACTION != 0 {
                    return Err(CfgError::new(ENOENT, cat!(b"Missing mandatory argument for status_manager '", sm.value,
                        b"' in block '", blk_and_name, b"': implemented action.")));
                }
            }
            1 => {
                let x = &sm.extra[0];
                if eq_nocase(x, b"removed") || eq_nocase(x, b"deleted") {
                    if sm_def.flags & crate::smi::SM_DELETED == 0 {
                        return Err(CfgError::inval(cat!(b"'", x, b"' is specified for status manager '", sm.value,
                            b"' whereas it cannot handle deleted entries.")));
                    }
                    p.manage_deleted = true;
                    p.implements = Some(lower(x));
                } else if sm_def.supports_action(x) {
                    p.implements = Some(lower(x));
                } else {
                    return Err(CfgError::inval(cat!(b"status manager '", sm.value, b"' does not support action '", x,
                        b"' in block '", blk_and_name, b"'.")));
                }
            }
            n => {
                return Err(CfgError::inval(cat!(format!("Too many arguments ({n}) found for status_manager parameter '"),
                    sm.value, b"', in block '", blk_and_name, b"'.")));
            }
        }
    }

    let lru = h::get_string(cfg, blk, block_name, b"default_lru_sort_attr", h::NO_WILDCARDS | h::MANDATORY, 1024, true)?;
    match lru.extra.len() {
        0 => p.default_lru_sort_order = SORT_ASC,
        1 => {
            let x = &lru.extra[0];
            p.default_lru_sort_order = if eq_nocase(x, b"ASC") {
                SORT_ASC
            } else if eq_nocase(x, b"DESC") {
                SORT_DESC
            } else {
                return Err(CfgError::inval(cat!(b"Invalid sort order '", x, b"' in block '", block_name,
                    b"': 'asc' or 'desc' expected")));
            };
        }
        _ => {
            return Err(CfgError::inval(cat!(b"Too many parameters found for default_lru_sort_attr =  '", lru.value,
                b"' in block '", block_name, b"': '(asc)' or '(desc)' expected")));
        }
    }
    let attr = boolexpr::str2lru_attr(reg, log, p.status_mgr, &lru.value);
    if attr == LRU_ATTR_INVAL {
        return Err(CfgError::inval(cat!(b"Attribute not supported for 'default_lru_sort_attr': Expected: ",
            boolexpr::ALLOWED_LRU_ATTRS_STR, b"...")));
    }
    p.default_lru_sort_attr = attr;

    let mut unique = true;
    let Some(scope) = cfg.get_item_by_name(blk, b"scope", &mut unique) else {
        return Err(CfgError::new(ENOENT, cat!(b"Missing mandatory parameter 'scope' in block '", blk_and_name, b"'")));
    };
    let scope_line = access::line(scope).to_string();
    if !unique {
        return Err(CfgError::new(EEXIST, cat!(b"Duplicate scope declaration in block '", blk_and_name, b"', line ",
            scope_line, b".")));
    }
    let not_scope = || CfgError::inval(cat!(b"Sub-block (or 'scope = all') is expected for 'scope' item in block '",
        blk_and_name, b"', line ", scope_line));
    match access::kind(scope) {
        Kind::Var => {
            let (_, value, extra) = access::key_value(scope).expect("a variable");
            if !eq_nocase(value, b"all") {
                return Err(not_scope());
            }
            if extra {
                return Err(CfgError::inval(cat!(b"Unexpected argument after 'scope = all' in block '", blk_and_name,
                    b"', line ", scope_line)));
            }
            p.scope = Node::Constant(true);
            p.scope_mask = AttrMask::NULL;
        }
        Kind::Block => {
            let ctx = Ctx { reg, log, uid_gid_as_numbers: env.uid_gid_as_numbers, smi: p.status_mgr };
            let (n, m) = boolexpr::get_bool_expr(&ctx, scope, b"scope")?;
            p.scope = n;
            p.scope_mask = m;
        }
        _ => return Err(not_scope()),
    }

    match h::get_string(cfg, blk, block_name, b"status_current", h::NO_WILDCARDS, 1024, true) {
        Ok(v) => {
            let Some(smi) = p.status_mgr else {
                return Err(CfgError::inval(cat!(b"Can't specify a 'status_current' parameter without a status manager, in block '",
                    blk_and_name, b"', line ", scope_line)));
            };
            p.status_current = reg.smi(smi).sm.status(&v.value);
            if p.status_current.is_none() {
                return Err(CfgError::inval(cat!(b"Invalid value for 'status_current' parameter in block '", blk_and_name,
                    b"', line ", scope_line, b": '", v.value, b"' (valid status expected)")));
            }
        }
        Err(e) if e.rc == ENOENT => {}
        Err(e) => return Err(e),
    }

    let expect = ["status_manager", "scope", "default_action", "default_lru_sort_attr", "status_current"];
    for w in h::unknown_parameters(cfg, blk, block_name, &expect) {
        logs::display(log, LVL_CRIT, "Config Check", &w);
    }
    Ok(p)
}

/// read_policy_definitions
pub fn read_definitions(cfg: &Config, reg: &mut Registry, env: &Env) -> Result<Policies, CfgError> {
    let mut pols = Policies::default();
    for item in &cfg.items {
        if access::kind(item) != Kind::Block {
            continue;
        }
        let block_name = access::name(item).expect("a block");
        if !eq_nocase(block_name, POLICY_DECLARATION) {
            continue;
        }
        let p = parse_policy_decl(cfg, item, block_name, &pols, reg, env)?;
        if p.manage_deleted {
            pols.manage_deleted = true;
        }
        pols.policy_list.push(p);
    }
    Ok(pols)
}

// --- the harness's dump (dump_defs in rust/difftest/c/cfgload.c) -------------------

pub fn dump(pols: &Policies, reg: &Registry, uid_gid_as_numbers: bool) -> Vec<u8> {
    use boolexpr::esc;
    let mut o = format!("defs count={} manage_deleted={}\n", pols.policy_list.len(), pols.manage_deleted as u32).into_bytes();
    for s in &reg.instances {
        o.extend_from_slice(format!("smi {} name=", s.smi_index).as_bytes());
        esc(&mut o, &s.instance_name);
        o.extend_from_slice(b" db_field=");
        esc(&mut o, &s.db_field);
        o.extend_from_slice(b" user_name=");
        esc(&mut o, &s.user_name);
        o.extend_from_slice(b" sm=");
        esc(&mut o, s.sm.name.as_bytes());
        o.extend_from_slice(format!(" info_offset={}\n", s.sm_info_offset).as_bytes());
    }
    for (i, a) in reg.attrs.iter().enumerate() {
        o.extend_from_slice(format!("sm_attr {i} db=").as_bytes());
        esc(&mut o, &a.db_attr_name);
        o.extend_from_slice(b" user=");
        esc(&mut o, &a.user_attr_name);
        o.extend_from_slice(format!(" smi={}\n", a.smi).as_bytes());
    }
    for p in &pols.policy_list {
        o.extend_from_slice(b"policy ");
        esc(&mut o, &p.name);
        o.extend_from_slice(format!(" action={}", p.default_action.type_value()).as_bytes());
        match &p.default_action {
            Action::Function(f) => {
                o.push(b':');
                esc(&mut o, f);
            }
            Action::Command(argv) => {
                for (i, a) in argv.iter().enumerate() {
                    o.push(if i == 0 { b':' } else { b',' });
                    esc(&mut o, a);
                }
            }
            _ => {}
        }
        o.extend_from_slice(format!(" run_mask={}", p.run_attr_mask.dump()).as_bytes());
        match p.status_mgr {
            Some(s) => o.extend_from_slice(format!(" smi={s}").as_bytes()),
            None => o.extend_from_slice(b" smi=-"),
        }
        o.extend_from_slice(b" implements=");
        match &p.implements {
            Some(x) => esc(&mut o, x),
            None => o.push(b'-'),
        }
        o.extend_from_slice(format!(" manage_deleted={} lru_attr={:x} lru_order={} scope_mask={} status_current=",
            p.manage_deleted as i32, p.default_lru_sort_attr, p.default_lru_sort_order, p.scope_mask.dump()).as_bytes());
        match p.status_current {
            Some(x) => esc(&mut o, x.as_bytes()),
            None => o.push(b'-'),
        }
        o.extend_from_slice(b"\n  scope ");
        boolexpr::dump(&mut o, &p.scope);
        o.extend_from_slice(b"\n  scope_str ");
        match boolexpr::to_str(&p.scope, uid_gid_as_numbers) {
            Some(s) => esc(&mut o, &s),
            None => esc(&mut o, b"<error>"),
        }
        o.push(b'\n');
    }
    o
}
