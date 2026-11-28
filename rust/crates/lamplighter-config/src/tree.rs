// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// The syntax tree and printer follow Robinhood's src/cfg_parsing/analyze.c
// (Copyright (C) 2004-2009 CEA/DAM, CeCILL-C).

//! The syntax tree of a configuration file, as Robinhood's C parser builds
//! it (`generic_item` in analyze.h). Names and values are bytes: the C
//! parser works on bytes, cuts them at 1023 bytes, and so does this one.

/// A comparison operator in a condition (`operator_t`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Op {
    Equal,
    Diff,
    Gt,
    GtEq,
    Lt,
    LtEq,
    /// `name(value)`: a function-like condition (`OP_CMD`).
    Cmd,
}

/// A boolean expression (`type_bool_expr`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BoolExpr {
    Condition { name: Vec<u8>, op: Op, value: Vec<u8>, args: Option<Vec<Vec<u8>>> },
    Not(Box<BoolExpr>),
    And(Box<BoolExpr>, Box<BoolExpr>),
    Or(Box<BoolExpr>, Box<BoolExpr>),
}

/// A set of fileclasses (`type_set`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SetExpr {
    Singleton(Vec<u8>),
    Not(Box<SetExpr>),
    Union(Box<SetExpr>, Box<SetExpr>),
    Inter(Box<SetExpr>, Box<SetExpr>),
}

/// One item of a configuration (`generic_item`). `line` is the line the C
/// parser records: the scanner's line when the item is built.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Item {
    /// `name [id] { ... }`. A block holding a boolean expression or a set
    /// has that single item as its content.
    Block { name: Vec<u8>, id: Vec<u8>, content: Vec<Item>, line: u32 },
    /// `name = value [(args)];`
    Affect { name: Vec<u8>, value: Vec<u8>, args: Option<Vec<Vec<u8>>>, line: u32 },
    BoolExpr { expr: BoolExpr, line: u32 },
    Set { set: SetExpr, line: u32 },
}

fn esc(out: &mut Vec<u8>, s: &[u8]) {
    out.push(b'"');
    for &c in s {
        if c < 0x20 || c >= 0x7f || c == b'"' || c == b'\\' {
            out.extend_from_slice(format!("\\x{c:02x}").as_bytes());
        } else {
            out.push(c);
        }
    }
    out.push(b'"');
}

fn esc_args(out: &mut Vec<u8>, args: &Option<Vec<Vec<u8>>>) {
    match args {
        None => out.push(b'-'),
        Some(a) => {
            out.push(b'[');
            for (i, v) in a.iter().enumerate() {
                if i > 0 {
                    out.push(b',');
                }
                esc(out, v);
            }
            out.push(b']');
        }
    }
}

fn dump_expr(out: &mut Vec<u8>, e: &BoolExpr) {
    match e {
        BoolExpr::Condition { name, op, value, args } => {
            out.extend_from_slice(b"cond(");
            esc(out, name);
            let o: &[u8] = match op {
                Op::Equal => b"==",
                Op::Diff => b"!=",
                Op::Gt => b">",
                Op::GtEq => b">=",
                Op::Lt => b"<",
                Op::LtEq => b"<=",
                Op::Cmd => b"cmd",
            };
            out.push(b' ');
            out.extend_from_slice(o);
            out.push(b' ');
            esc(out, value);
            out.extend_from_slice(b" args=");
            esc_args(out, args);
            out.push(b')');
        }
        BoolExpr::Not(a) => {
            out.extend_from_slice(b"not(");
            dump_expr(out, a);
            out.push(b')');
        }
        BoolExpr::And(a, b) | BoolExpr::Or(a, b) => {
            out.extend_from_slice(if matches!(e, BoolExpr::And(..)) { b"and(" } else { b"or(" });
            dump_expr(out, a);
            out.push(b',');
            dump_expr(out, b);
            out.push(b')');
        }
    }
}

fn dump_set(out: &mut Vec<u8>, s: &SetExpr) {
    match s {
        SetExpr::Singleton(n) => {
            out.extend_from_slice(b"set(");
            esc(out, n);
            out.push(b')');
        }
        SetExpr::Not(a) => {
            out.extend_from_slice(b"setnot(");
            dump_set(out, a);
            out.push(b')');
        }
        SetExpr::Union(a, b) | SetExpr::Inter(a, b) => {
            out.extend_from_slice(if matches!(s, SetExpr::Union(..)) { b"union(" } else { b"inter(" });
            dump_set(out, a);
            out.push(b',');
            dump_set(out, b);
            out.push(b')');
        }
    }
}

/// The canonical dump of a tree: the same text as rust/difftest/c/cfgdump.c
/// prints for the C parser (every name, ID, value, argument and line).
pub fn dump(items: &[Item]) -> Vec<u8> {
    let mut out = Vec::new();
    dump_items(&mut out, items, 0);
    out
}

fn dump_items(out: &mut Vec<u8>, items: &[Item], depth: usize) {
    for it in items {
        out.extend(std::iter::repeat(b' ').take(depth * 2));
        match it {
            Item::Block { name, id, content, line } => {
                out.extend_from_slice(format!("BLOCK line={line} name=").as_bytes());
                esc(out, name);
                out.extend_from_slice(b" id=");
                esc(out, id);
                out.push(b'\n');
                dump_items(out, content, depth + 1);
            }
            Item::Affect { name, value, args, line } => {
                out.extend_from_slice(format!("AFFECT line={line} name=").as_bytes());
                esc(out, name);
                out.extend_from_slice(b" value=");
                esc(out, value);
                out.extend_from_slice(b" args=");
                esc_args(out, args);
                out.push(b'\n');
            }
            Item::BoolExpr { expr, line } => {
                out.extend_from_slice(format!("EXPR line={line} ").as_bytes());
                dump_expr(out, expr);
                out.push(b'\n');
            }
            Item::Set { set, line } => {
                out.extend_from_slice(format!("SET line={line} ").as_bytes());
                dump_set(out, set);
                out.push(b'\n');
            }
        }
    }
}

/// The tree as Robinhood's `rh_config_Print` prints it, byte for byte.
pub fn c_print(items: &[Item]) -> Vec<u8> {
    let mut out = Vec::new();
    c_print_list(&mut out, items, 0);
    out
}

fn pad(out: &mut Vec<u8>, indent: usize) {
    // fprintf("%*s", indent, " "): at least the one space
    out.extend(std::iter::repeat(b' ').take(indent.max(1)));
}

fn c_print_expr(out: &mut Vec<u8>, e: &BoolExpr) {
    match e {
        BoolExpr::Condition { name, op, value, args } => {
            out.extend_from_slice(name);
            let o: &[u8] = match op {
                Op::Equal => b"==",
                Op::Diff => b"!=",
                Op::Gt => b">",
                Op::GtEq => b">=",
                Op::Lt => b"<",
                Op::LtEq => b"<=",
                Op::Cmd => b":",
            };
            out.push(b' ');
            out.extend_from_slice(o);
            out.push(b' ');
            out.extend_from_slice(value);
            if let Some(a) = args.as_ref().filter(|a| !a.is_empty()) {
                out.extend_from_slice(b" (");
                for (i, v) in a.iter().enumerate() {
                    if i > 0 {
                        out.extend_from_slice(b", ");
                    }
                    out.extend_from_slice(v);
                }
                out.push(b')');
            }
        }
        BoolExpr::Not(a) => {
            out.extend_from_slice(b"NOT (");
            c_print_expr(out, a);
            out.push(b')');
        }
        BoolExpr::And(a, b) | BoolExpr::Or(a, b) => {
            out.push(b'(');
            c_print_expr(out, a);
            out.extend_from_slice(if matches!(e, BoolExpr::And(..)) { b") AND (" } else { b") OR (" });
            c_print_expr(out, b);
            out.push(b')');
        }
    }
}

fn c_print_set(out: &mut Vec<u8>, s: &SetExpr) {
    match s {
        SetExpr::Singleton(n) => {
            out.push(b'{');
            out.extend_from_slice(n);
            out.push(b'}');
        }
        SetExpr::Not(a) => {
            out.extend_from_slice(b"NOT (");
            c_print_set(out, a);
            out.push(b')');
        }
        SetExpr::Union(a, b) | SetExpr::Inter(a, b) => {
            out.push(b'(');
            c_print_set(out, a);
            out.extend_from_slice(if matches!(s, SetExpr::Union(..)) { b") UNION (" } else { b") INTER (" });
            c_print_set(out, b);
            out.push(b')');
        }
    }
}

fn c_print_list(out: &mut Vec<u8>, items: &[Item], indent: usize) {
    for it in items {
        match it {
            Item::Block { name, content, .. } => {
                pad(out, indent);
                out.extend_from_slice(b"<BLOCK '");
                out.extend_from_slice(name);
                out.extend_from_slice(b"'>\n");
                c_print_list(out, content, indent + 3);
                pad(out, indent);
                out.extend_from_slice(b"</BLOCK '");
                out.extend_from_slice(name);
                out.extend_from_slice(b"'>\n");
            }
            Item::Affect { name, value, args, .. } => {
                pad(out, indent);
                out.extend_from_slice(b"<AFFECT> ");
                out.extend_from_slice(name);
                out.extend_from_slice(b"='");
                out.extend_from_slice(value);
                out.push(b'\'');
                if let Some(a) = args {
                    for (i, v) in a.iter().enumerate() {
                        out.extend_from_slice(if i == 0 { b"('" } else { b", '" });
                        out.extend_from_slice(v);
                        out.push(b'\'');
                    }
                    out.push(b')');
                }
                out.extend_from_slice(b"</AFFECT>\n");
            }
            Item::Set { set, .. } => {
                pad(out, indent);
                c_print_set(out, set);
                out.push(b'\n');
            }
            Item::BoolExpr { expr, .. } => {
                pad(out, indent);
                c_print_expr(out, expr);
                out.push(b'\n');
            }
        }
    }
}
