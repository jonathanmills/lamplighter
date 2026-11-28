// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// Translated from Robinhood's src/cfg_parsing/rbh_boolexpr.c and the
// uid/gid helpers of src/common/rbh_misc.c (Copyright (C) CEA/DAM,
// CeCILL-C).

//! Boolean expressions of policies and fileclasses (`bool_node_t`):
//! building them from a configuration block (GetBoolExpr), with the C
//! code's checks, messages and attribute masks, and printing them
//! (BoolExpr2str).
//!
//! Two C behaviours are undefined, so they are pinned here:
//! - a status manager attribute's condition reads its flags past the end
//!   of criteria_descr[]; in the reference build that memory is 0, and so
//!   are the flags here;
//! - with uid_gid_as_numbers, an unknown user or group name leaves the
//!   uid/gid uninitialised in C (getpwnam_r's "not found" is not checked);
//!   here it fails as C intends: "couldn't resolve uid for user '...'".

use crate::access::{self, Kind};
use crate::attrs::{self, AttrMask};
use crate::cat;
use crate::cvt::{self, eq_nocase};
use crate::helpers::{CfgError, ENOENT};
use crate::logs::{self, LogState, LVL_CRIT};
use crate::smi::{CritType, Registry, SmAttr, SM_DELETED};
use crate::tree::{self, Item, Op};

/// compare_direction_t
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Comp {
    None = 0,
    GrThan,
    GrThanEq,
    LsThan,
    LsThanEq,
    Equal,
    Diff,
    Like,
    Unlike,
}

/// compare_criteria_t (the Lustre criteria exist only in Lustre builds,
/// which shifts the later values, as in C)
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Crit {
    Tree,
    Path,
    Name,
    Iname,
    Type,
    Owner,
    Group,
    Size,
    Depth,
    Dircount,
    Nlink,
    LastAccess,
    LastMod,
    LastMdchange,
    Creation,
    RmTime,
    Pool,
    Ost,
    Projid,
    Fileclass,
    Status,
    Xattr,
    SmInfo,
}

const LUSTRE: bool = cfg!(feature = "lustre");

impl Crit {
    /// The C enum value.
    pub fn value(self) -> i32 {
        let v = self as i32;
        if LUSTRE || v < Crit::Pool as i32 {
            v
        } else {
            v - 3
        }
    }
}

/// enum compare_flags
pub const CMP_FLG_ANY_LEVEL: i32 = 1 << 0;
pub const CMP_FLG_INSENSITIVE: i32 = 1 << 1;

/// The value of a condition (compare_value_t), as its criteria reads it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Value {
    Str(Vec<u8>),
    Size(u64),
    Int(u32),
    Duration(i64),
    Type(i32),
}

/// compare_triplet_t
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Triplet {
    pub flags: i32,
    pub crit: Crit,
    /// for xattrs and status manager attributes
    pub attr_name: Vec<u8>,
    pub op: Comp,
    pub val: Value,
}

/// bool_op_t
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BoolOp {
    Not = 1,
    Or = 2,
    And = 3,
}

/// bool_node_t
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Node {
    Constant(bool),
    Condition(Box<Triplet>),
    Unary(BoolOp, Box<Node>),
    Binary(BoolOp, Box<Node>, Box<Node>),
}

// parsing flags of criteria_descr (rbh_cfg_helpers.h)
const NOT_EMPTY: u32 = 1 << 0;
const REMOVE_FINAL_SLASH: u32 = 1 << 1;
const NO_WILDCARDS: u32 = 1 << 2;
const POSITIVE: u32 = 1 << 3;
const COMPARABLE: u32 = 1 << 4;
const NO_SLASH: u32 = 1 << 5;
const ALLOW_ANY_DEPTH: u32 = 1 << 6;
const STATUS: u32 = 1 << 7;
const XATTR: u32 = 1 << 8;

pub const XATTR_PREFIX: &str = "xattr";

/// criteria_descr[]: name, standard attribute mask, type, parsing flags,
/// comparison flags
struct CritDescr {
    crit: Crit,
    name: &'static str,
    mask: u32,
    ty: CritType,
    flags: u32,
    cmp_flags: i32,
}

const fn d(crit: Crit, name: &'static str, index: u32, ty: CritType, flags: u32, cmp_flags: i32) -> CritDescr {
    CritDescr { crit, name, mask: attrs::mask(index), ty, flags, cmp_flags }
}

use crate::attrs::*;

/// XATTR_NEED: without Lustre FIDs, xattrs are read through the path.
const XATTR_NEED: u32 = if cfg!(all(feature = "lustre", feature = "fid")) { 0 } else { mask(ATTR_INDEX_FULLPATH) };
const CMP: u32 = POSITIVE | COMPARABLE;

static CRITERIA_COMMON: &[CritDescr] = &[
    d(Crit::Tree, "tree", ATTR_INDEX_FULLPATH, CritType::String, ALLOW_ANY_DEPTH | NOT_EMPTY | REMOVE_FINAL_SLASH, 0),
    d(Crit::Path, "path", ATTR_INDEX_FULLPATH, CritType::String, ALLOW_ANY_DEPTH | NOT_EMPTY | REMOVE_FINAL_SLASH, 0),
    d(Crit::Name, "name", ATTR_INDEX_NAME, CritType::String, NOT_EMPTY | NO_SLASH, 0),
    d(Crit::Iname, "iname", ATTR_INDEX_NAME, CritType::String, NOT_EMPTY | NO_SLASH, CMP_FLG_INSENSITIVE),
    d(Crit::Type, "type", ATTR_INDEX_TYPE, CritType::Type, 0, 0),
    d(Crit::Owner, "owner", ATTR_INDEX_UID, CritType::String, NOT_EMPTY, 0),
    d(Crit::Group, "group", ATTR_INDEX_GID, CritType::String, NOT_EMPTY, 0),
    d(Crit::Size, "size", ATTR_INDEX_SIZE, CritType::Size, CMP, 0),
    d(Crit::Depth, "depth", ATTR_INDEX_DEPTH, CritType::Int, CMP, 0),
    d(Crit::Dircount, "dircount", ATTR_INDEX_DIRCOUNT, CritType::Int, CMP, 0),
    d(Crit::Nlink, "nlink", ATTR_INDEX_NLINK, CritType::Int, CMP, 0),
    d(Crit::LastAccess, "last_access", ATTR_INDEX_LAST_ACCESS, CritType::Duration, CMP, 0),
    d(Crit::LastMod, "last_mod", ATTR_INDEX_LAST_MOD, CritType::Duration, CMP, 0),
    d(Crit::LastMdchange, "last_mdchange", ATTR_INDEX_LAST_MDCHANGE, CritType::Duration, CMP, 0),
    d(Crit::Creation, "creation", ATTR_INDEX_CREATION_TIME, CritType::Duration, CMP, 0),
    d(Crit::RmTime, "rm_time", ATTR_INDEX_RM_TIME, CritType::Duration, CMP | STATUS, 0),
];

static CRITERIA_LUSTRE: &[CritDescr] = &[
    d(Crit::Pool, "ost_pool", ATTR_INDEX_STRIPE_INFO, CritType::String, 0, 0),
    d(Crit::Ost, "ost_index", ATTR_INDEX_STRIPE_ITEMS, CritType::Int, POSITIVE, 0),
    d(Crit::Projid, "projid", ATTR_INDEX_PROJID, CritType::Int, CMP, 0),
];

static CRITERIA_LAST: &[CritDescr] = &[
    d(Crit::Fileclass, "fileclass", ATTR_INDEX_FILECLASS, CritType::String, NO_SLASH, CMP_FLG_INSENSITIVE),
    CritDescr { crit: Crit::Status, name: "status", mask: 0, ty: CritType::String, flags: STATUS | NO_WILDCARDS,
                cmp_flags: CMP_FLG_INSENSITIVE },
    CritDescr { crit: Crit::Xattr, name: XATTR_PREFIX, mask: XATTR_NEED, ty: CritType::String, flags: XATTR, cmp_flags: 0 },
];

/// The standard criteria, in the C enum's order.
fn criteria() -> impl Iterator<Item = &'static CritDescr> {
    let lustre: &[CritDescr] = if LUSTRE { CRITERIA_LUSTRE } else { &[] };
    CRITERIA_COMMON.iter().chain(lustre).chain(CRITERIA_LAST)
}

fn descr(crit: Crit) -> &'static CritDescr {
    criteria().find(|c| c.crit == crit).expect("a standard criteria")
}

/// criteria2str
pub fn criteria2str(crit: Crit) -> &'static str {
    if crit == Crit::SmInfo {
        "?"
    } else {
        descr(crit).name
    }
}

/// What a criteria name designates (str2criteria).
pub enum Criteria {
    Std(Crit),
    SmInfo(SmAttr),
}

/// str2criteria
pub fn str2criteria(reg: &Registry, log: &LogState, smi: Option<u32>, s: &[u8]) -> Option<Criteria> {
    let prefix = cat!(XATTR_PREFIX, b".");
    if s.len() >= prefix.len() && eq_nocase(&s[..prefix.len()], &prefix) {
        return Some(Criteria::Std(Crit::Xattr));
    }
    if let Some(c) = criteria().find(|c| c.crit != Crit::Xattr && eq_nocase(s, c.name.as_bytes())) {
        return Some(Criteria::Std(c.crit));
    }
    reg.attr_get(log, smi, s).map(Criteria::SmInfo)
}

/// LRU_ATTR_NONE, LRU_ATTR_INVAL
pub const LRU_ATTR_NONE: u32 = ATTR_INDEX_FLG_UNSPEC;
pub const LRU_ATTR_INVAL: u32 = ATTR_INDEX_FLG_UNSPEC | 0x1;
pub const ALLOWED_LRU_ATTRS_STR: &str = "none, creation, last_access, last_mod, rm_time, size, or status manager specific.";

/// str2lru_attr
pub fn str2lru_attr(reg: &Registry, log: &LogState, smi: Option<u32>, s: &[u8]) -> u32 {
    for (crit, index) in [(Crit::LastAccess, ATTR_INDEX_LAST_ACCESS), (Crit::LastMod, ATTR_INDEX_LAST_MOD),
                          (Crit::LastMdchange, ATTR_INDEX_LAST_MDCHANGE), (Crit::Creation, ATTR_INDEX_CREATION_TIME),
                          (Crit::RmTime, ATTR_INDEX_RM_TIME), (Crit::Size, ATTR_INDEX_SIZE)] {
        if eq_nocase(s, criteria2str(crit).as_bytes()) {
            return index;
        }
    }
    if eq_nocase(s, b"none") {
        return LRU_ATTR_NONE;
    }
    match reg.attr_get(log, smi, s) {
        Some(a) if a.crit_type() == CritType::Duration => a.index(),
        _ => LRU_ATTR_INVAL,
    }
}

const TYPE_NAMES: [&str; 8] = ["?", "symlink", "directory", "file", "char", "block", "fifo", "socket"];

/// str2type (0: TYPE_NONE)
fn str2type(s: &[u8]) -> i32 {
    TYPE_NAMES.iter().position(|n| eq_nocase(s, n.as_bytes())).unwrap_or(0) as i32
}

/// type2str
fn type2str(t: i32) -> &'static str {
    TYPE_NAMES.get(t as usize).copied().unwrap_or(TYPE_NAMES[0])
}

fn wildcards_in(s: &[u8]) -> bool {
    s.iter().any(|c| b"*?[]{}".contains(c))
}

fn find(hay: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if from > hay.len() {
        return None;
    }
    hay[from..].windows(needle.len()).position(|w| w == needle).map(|i| i + from)
}

/// str_subst: replace every occurrence, left to right.
fn str_subst(s: &mut Vec<u8>, from: &[u8], to: &[u8]) {
    let mut pos = 0;
    while let Some(i) = find(s, from, pos) {
        s.splice(i..i + from.len(), to.iter().copied());
        pos = i + to.len();
    }
}

/// process_any_level_condition: check a '**' expression and adapt it to
/// fnmatch ('?' -> '[!/]', '**' -> '*').
fn process_any_level_condition(s: &mut Vec<u8>) -> Result<(), CfgError> {
    let len = s.len();
    let mut cur = find(s, b"**", 0);
    while let Some(i) = cur {
        let before_bad = i > 0 && s[i - 1] != b'/';
        let after_bad = len > 0 && i + 2 <= len - 1 && s[i + 2] != b'/';
        if before_bad || after_bad {
            return Err(CfgError::inval(cat!(b"Character before and after '**' must be a '/' in '", s, b"'")));
        }
        cur = find(s, b"**", i + 2);
    }
    let mut cur = s.iter().position(|&c| c == b'*');
    while let Some(i) = cur {
        if s.get(i + 1) != Some(&b'*') {
            return Err(CfgError::inval(cat!(b"Single wildcard '*' cannot be used in the same expression as double wildcard '**' in '",
                s, b"'")));
        }
        cur = s.get(i + 2..).and_then(|r| r.iter().position(|&c| c == b'*')).map(|j| j + i + 2);
    }
    str_subst(s, b"?", b"[!/]");
    str_subst(s, b"**", b"*");
    Ok(())
}

/// set_uid_val / set_gid_val, with uid_gid_as_numbers set: the id, or
/// None after logging why (the C caller then fails with an empty message).
fn name2id(log: &LogState, name: &[u8], group: bool) -> Option<u32> {
    let (func, what, kind) = if group { ("set_gid_val", "GID", "group") } else { ("set_uid_val", "UID", "user") };
    if wildcards_in(name) {
        logs::display(log, LVL_CRIT, func, &cat!(b"ERROR: Wilcards not allowed in ", kind, b" name"));
        return None;
    }
    let (n, errno, digits) = cvt::strtol0(name);
    if (errno == libc::ERANGE && (n == i64::MAX || n == i64::MIN)) || (errno != 0 && n == 0) || !digits {
        let id = lookup_id(name, group);
        if id.is_none() {
            let (short, idname) = if group { ("gid", "group") } else { ("uid", "user") };
            logs::display(log, LVL_CRIT, func, &cat!(b"couldn't resolve ", short, b" for ", idname, b" '", name, b"'"));
        }
        return id;
    }
    if n < 0 {
        logs::display(log, LVL_CRIT, func, format!("ERROR: Given {what} is negative ({n})").as_bytes());
        return None;
    }
    if n > u32::MAX as i64 {
        logs::display(log, LVL_CRIT, func, format!("ERROR: Given {what} is too big ({n})").as_bytes());
        return None;
    }
    Some(n as u32)
}

/// getpwnam_r / getgrnam_r with the C code's 4096-byte buffer: the id,
/// or None (an error, or no such name).
fn lookup_id(name: &[u8], group: bool) -> Option<u32> {
    let cname = std::ffi::CString::new(crate::lexer::cstr(name)).ok()?;
    let mut buf = vec![0 as libc::c_char; 4096];
    // SAFETY: valid pointers and buffer length; result is checked.
    unsafe {
        if group {
            let mut grp: libc::group = std::mem::zeroed();
            let mut result: *mut libc::group = std::ptr::null_mut();
            let rc = libc::getgrnam_r(cname.as_ptr(), &mut grp, buf.as_mut_ptr(), buf.len(), &mut result);
            (rc == 0 && !result.is_null()).then_some(grp.gr_gid)
        } else {
            let mut pw: libc::passwd = std::mem::zeroed();
            let mut result: *mut libc::passwd = std::ptr::null_mut();
            let rc = libc::getpwnam_r(cname.as_ptr(), &mut pw, buf.as_mut_ptr(), buf.len(), &mut result);
            (rc == 0 && !result.is_null()).then_some(pw.pw_uid)
        }
    }
}

/// syntax2conf_comparator
fn comparator(op: Op) -> Comp {
    match op {
        Op::Equal => Comp::Equal,
        Op::Diff => Comp::Diff,
        Op::Gt => Comp::GrThan,
        Op::GtEq => Comp::GrThanEq,
        Op::Lt => Comp::LsThan,
        Op::LtEq => Comp::LsThanEq,
        Op::Cmd => Comp::None,
    }
}

/// What interpret_condition needs from its context.
pub struct Ctx<'a> {
    pub reg: &'a Registry,
    pub log: &'a LogState,
    /// global_config.uid_gid_as_numbers
    pub uid_gid_as_numbers: bool,
    /// the policy's status manager instance, in a policy scope
    pub smi: Option<u32>,
}

/// strncpy into a C buffer of `size` bytes
fn cut(s: &[u8], size: usize) -> Vec<u8> {
    crate::lexer::strncpy(s, size)
}

const RBH_NAME_MAX: usize = 256;
const RBH_PATH_MAX: usize = 4096;

/// criteria2condition
#[allow(clippy::too_many_arguments)]
fn criteria2condition(ctx: &Ctx, name: &[u8], op: Op, value: &[u8], mask: &mut AttrMask, crit: Crit, ty: CritType,
                      crit_mask: AttrMask, flags: u32, cmp_flags: i32) -> Result<Triplet, CfgError> {
    let fail = |m: Vec<u8>| Err(CfgError::inval(m));
    if flags & STATUS != 0 {
        let Some(smi) = ctx.smi else {
            return fail(cat!(b"'", name, b"' criteria is not expected in this context"));
        };
        let smi = ctx.reg.smi(smi);
        if eq_nocase(name, b"status") {
            mask.set_index(ATTR_INDEX_FLG_STATUS | smi.smi_index);
        } else if smi.sm.flags & SM_DELETED != 0 {
            *mask = mask.or(&crit_mask);
        } else {
            return fail(cat!(b"Attribute '", name,
                b"' only applies to deleted entries but status manager '", smi.sm.name,
                b"' does not manage deleted entries"));
        }
    } else {
        *mask = mask.or(&crit_mask);
    }
    let mut t = Triplet { flags: cmp_flags, crit, attr_name: Vec::new(), op: comparator(op), val: Value::Int(0) };
    if crit == Crit::SmInfo {
        t.attr_name = cut(name, RBH_NAME_MAX);
    }
    match ty {
        CritType::String => {
            if flags & NOT_EMPTY != 0 && value.is_empty() {
                return fail(cat!(b"non-empty string expected for ", name, b" parameter"));
            }
            if flags & NO_SLASH != 0 && value.contains(&b'/') {
                return fail(cat!(b"no slash (/) expected in ", name, b" parameter"));
            }
            if ctx.uid_gid_as_numbers && (crit == Crit::Owner || crit == Crit::Group) {
                let Some(id) = name2id(ctx.log, value, crit == Crit::Group) else {
                    return fail(Vec::new());
                };
                t.val = Value::Int(id);
                // (COMP_LIKE and COMP_UNLIKE cannot occur here)
                return Ok(t);
            }
            if wildcards_in(value) {
                if flags & NO_WILDCARDS != 0 {
                    return fail(cat!(b"No wildcard is allowed in ", name, b" criteria"));
                }
                if t.op == Comp::Equal {
                    t.op = Comp::Like;
                } else if t.op == Comp::Diff {
                    t.op = Comp::Unlike;
                }
            }
            let mut v = cut(value, RBH_PATH_MAX);
            if flags & XATTR != 0 {
                let dot = name.iter().position(|&c| c == b'.').expect("xattr.<name>");
                t.attr_name = cut(&name[dot + 1..], RBH_NAME_MAX);
            } else if flags & STATUS != 0 && eq_nocase(name, b"status") {
                let sm = ctx.reg.smi(ctx.smi.unwrap()).sm;
                if sm.status(&v).is_none() && !v.is_empty() {
                    let allowed = cut(&sm.allowed(), RBH_NAME_MAX);
                    return fail(cat!(b"Invalid status '", value, b"' for '", sm.name,
                        b"' status manager: allowed values are ", allowed));
                }
            } else if find(&v, b"**", 0).is_some() {
                if flags & ALLOW_ANY_DEPTH != 0 {
                    process_any_level_condition(&mut v)?;
                    t.flags |= CMP_FLG_ANY_LEVEL;
                } else {
                    return fail(cat!(b"double star wildcard (**) not expected in ", name, b" parameter"));
                }
            }
            if flags & REMOVE_FINAL_SLASH != 0 && v.len() > 1 && v.last() == Some(&b'/') {
                v.pop();
            }
            t.val = Value::Str(v);
        }
        CritType::Size => {
            let s = cvt::str2size(value);
            if s == u64::MAX {
                return fail(cat!(name, b" criteria: invalid format for size: '", value, b"'"));
            }
            t.val = Value::Size(s);
        }
        CritType::Int => {
            let i = cvt::str2int(value) as u32;
            if i == u32::MAX {
                return fail(cat!(name, b" criteria: integer expected: '", value, b"'"));
            }
            t.val = Value::Int(i);
        }
        CritType::Bool => {
            let b = cvt::str2bool(value);
            if b == -1 {
                return fail(cat!(name, b" criteria: boolean expected (0, 1, true, false, yes, no, enabled, disabled): '",
                    value, b"'"));
            }
            t.val = Value::Int(b as u32);
        }
        CritType::Duration => {
            let dur = cvt::str2duration(value) as i64;
            if dur == -1 {
                return fail(cat!(name, b" criteria: duration expected: '", value, b"'"));
            }
            if flags & POSITIVE != 0 && dur < 0 {
                return fail(cat!(b"Positive value expected for ", name, b" criteria"));
            }
            t.val = Value::Duration(dur);
        }
        CritType::Type => {
            let ty = str2type(value);
            if ty == 0 {
                return fail(b"Illegal condition on type: file, directory, symlink, chr, blk, fifo or sock expected.".to_vec());
            }
            t.val = Value::Type(ty);
        }
        CritType::Int64 | CritType::Float => {
            return Err(CfgError::new(95, cat!(b"Unsupported criteria type for '", name, b"'")));
        }
    }
    if flags & COMPARABLE == 0 && ![Comp::Equal, Comp::Diff, Comp::Like, Comp::Unlike].contains(&t.op) {
        return fail(cat!(b"Illegal comparator for ", name, b" criteria: == or != expected"));
    }
    Ok(t)
}

/// interpret_condition
fn interpret_condition(ctx: &Ctx, name: &[u8], op: Op, value: &[u8], mask: &mut AttrMask) -> Result<Triplet, CfgError> {
    match str2criteria(ctx.reg, ctx.log, ctx.smi, name) {
        None => Err(CfgError::inval(cat!(b"Unknown or unsupported criteria '", name, b"'"))),
        Some(Criteria::SmInfo(a)) => {
            let ty = a.crit_type();
            let flags = if matches!(ty, CritType::Duration | CritType::Size | CritType::Int | CritType::Int64 | CritType::Float) {
                COMPARABLE
            } else {
                0
            };
            let mut m = AttrMask::NULL;
            m.set_index(a.index());
            criteria2condition(ctx, name, op, value, mask, Crit::SmInfo, ty, m, flags, 0)
        }
        Some(Criteria::Std(crit)) => {
            let dsc = descr(crit);
            criteria2condition(ctx, name, op, value, mask, crit, dsc.ty, AttrMask::std(dsc.mask), dsc.flags, dsc.cmp_flags)
        }
    }
}

/// build_bool_expr
fn build(ctx: &Ctx, e: &tree::BoolExpr, mask: &mut AttrMask) -> Result<Node, CfgError> {
    Ok(match e {
        tree::BoolExpr::Condition { name, op, value, .. } => {
            Node::Condition(Box::new(interpret_condition(ctx, name, *op, value, mask)?))
        }
        tree::BoolExpr::Not(a) => Node::Unary(BoolOp::Not, Box::new(build(ctx, a, mask)?)),
        tree::BoolExpr::And(a, b) => {
            let a = build(ctx, a, mask)?;
            Node::Binary(BoolOp::And, Box::new(a), Box::new(build(ctx, b, mask)?))
        }
        tree::BoolExpr::Or(a, b) => {
            let a = build(ctx, a, mask)?;
            Node::Binary(BoolOp::Or, Box::new(a), Box::new(build(ctx, b, mask)?))
        }
    })
}

/// GetBoolExpr: the expression of a block and its attribute mask.
pub fn get_bool_expr(ctx: &Ctx, block: &Item, block_name: &[u8]) -> Result<(Node, AttrMask), CfgError> {
    if access::kind(block) != Kind::Block {
        return Err(CfgError::inval(cat!(b"'", block_name, b"' is expected to be a block")));
    }
    let content = access::content(block);
    let Some(sub) = content.first() else {
        return Err(CfgError::new(ENOENT, cat!(b"'", block_name, b"' block is empty, line ",
            access::line(block).to_string())));
    };
    let Item::BoolExpr { expr, line } = sub else {
        return Err(CfgError::inval(cat!(b"Boolean expression expected in block '", block_name, b"', line ",
            access::line(sub).to_string())));
    };
    if content.len() > 1 {
        return Err(CfgError::inval(cat!(b"A single boolean expression is expected in block '", block_name,
            b"', line ", line.to_string())));
    }
    let mut mask = AttrMask::NULL;
    match build(ctx, expr, &mut mask) {
        Ok(n) => Ok((n, mask)),
        Err(mut e) => {
            e.msg.extend_from_slice(format!(", line {line}").as_bytes());
            Err(e)
        }
    }
}

// --- printing -------------------------------------------------------------------

/// op2str
fn op2str(op: Comp) -> &'static str {
    match op {
        Comp::GrThan => ">",
        Comp::GrThanEq => ">=",
        Comp::LsThan => "<",
        Comp::LsThanEq => "<=",
        Comp::Equal => "==",
        Comp::Diff => "<>",
        Comp::Like => " =~ ",
        Comp::Unlike => " !~ ",
        Comp::None => "?",
    }
}

fn val_str(t: &Triplet) -> &[u8] {
    match &t.val {
        Value::Str(s) => s,
        _ => b"",
    }
}

fn val_int(t: &Triplet) -> u32 {
    match t.val {
        Value::Int(i) => i,
        _ => 0,
    }
}

/// print_condition (None: -EINVAL)
fn print_condition(t: &Triplet, uid_gid_as_numbers: bool) -> Option<Vec<u8>> {
    let head = cat!(criteria2str(t.crit), b" ", op2str(t.op), b" ");
    Some(match t.crit {
        Crit::Tree | Crit::Path | Crit::Name | Crit::Iname | Crit::Fileclass | Crit::Pool => {
            cat!(head, b"\"", val_str(t), b"\"")
        }
        Crit::Type => cat!(head, b"\"", type2str(match t.val { Value::Type(x) => x, _ => 0 }), b"\""),
        Crit::Depth | Crit::Ost | Crit::Dircount | Crit::Nlink => cat!(head, (val_int(t) as i32).to_string()),
        Crit::Size => cat!(head, cvt::format_file_size(match t.val { Value::Size(s) => s, _ => 0 })),
        Crit::Owner | Crit::Group => {
            if uid_gid_as_numbers {
                cat!(head, (val_int(t) as i32).to_string())
            } else {
                cat!(head, b"\"", val_str(t), b"\"")
            }
        }
        Crit::LastAccess | Crit::LastMod | Crit::LastMdchange | Crit::Creation | Crit::RmTime => {
            cat!(head, cvt::format_duration_float(match t.val { Value::Duration(x) => x, _ => 0 }))
        }
        Crit::Xattr => cat!(XATTR_PREFIX, b".", t.attr_name, b" ", op2str(t.op), b" ", val_str(t)),
        Crit::Projid | Crit::Status | Crit::SmInfo => return None,
    })
}

/// BoolExpr2str (None: the C function's error)
pub fn to_str(n: &Node, uid_gid_as_numbers: bool) -> Option<Vec<u8>> {
    Some(match n {
        Node::Unary(op, a) => {
            if *op != BoolOp::Not {
                return None;
            }
            cat!(b"NOT (", to_str(a, uid_gid_as_numbers)?, b")")
        }
        Node::Binary(op, a, b) => {
            let a = to_str(a, uid_gid_as_numbers)?;
            let sep: &[u8] = if *op == BoolOp::Or { b") OR (" } else { b") AND (" };
            cat!(b"(", a, sep, to_str(b, uid_gid_as_numbers)?, b")")
        }
        Node::Condition(t) => print_condition(t, uid_gid_as_numbers)?,
        Node::Constant(c) => if *c { b"yes".to_vec() } else { b"no".to_vec() },
    })
}

// --- the side-by-side harness's dump (rust/difftest/c/cfgload.c) ---------------

pub fn esc(out: &mut Vec<u8>, s: &[u8]) {
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

/// dump_node
pub fn dump(out: &mut Vec<u8>, n: &Node) {
    match n {
        Node::Constant(c) => out.extend_from_slice(format!("CONST({})", *c as i32).as_bytes()),
        Node::Unary(op, a) => {
            out.extend_from_slice(format!("UNARY{}(", *op as i32).as_bytes());
            dump(out, a);
            out.push(b')');
        }
        Node::Binary(op, a, b) => {
            out.extend_from_slice(format!("BINARY{}(", *op as i32).as_bytes());
            dump(out, a);
            out.extend_from_slice(b", ");
            dump(out, b);
            out.push(b')');
        }
        Node::Condition(t) => {
            out.extend_from_slice(format!("COND{{crit={} op={} flags={}", t.crit.value(), t.op as i32, t.flags).as_bytes());
            if t.crit == Crit::Xattr || t.crit == Crit::SmInfo {
                out.extend_from_slice(b" attr=");
                esc(out, &t.attr_name);
            }
            match &t.val {
                Value::Str(s) => {
                    out.extend_from_slice(b" str=");
                    esc(out, s);
                }
                Value::Size(s) => out.extend_from_slice(format!(" size={s}").as_bytes()),
                Value::Int(i) => out.extend_from_slice(format!(" int={i}").as_bytes()),
                Value::Duration(x) => out.extend_from_slice(format!(" duration={x}").as_bytes()),
                Value::Type(x) => out.extend_from_slice(format!(" type={x}").as_bytes()),
            }
            out.push(b'}');
        }
    }
}
