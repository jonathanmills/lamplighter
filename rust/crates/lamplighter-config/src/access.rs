// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// Translated from Robinhood's src/cfg_parsing/config_parsing.c
// (Copyright (C) 2004-2009 CEA/DAM, CeCILL-C).

//! Access to a parsed configuration, as `rh_config_*` in config_parsing.c:
//! items are looked up by name case-insensitively (strncasecmp), a lookup
//! can require the name to be unique (and then returns the conflicting
//! item), and every item found is marked "read", which decides the
//! "unknown parameter" warnings later.

use std::cell::RefCell;
use std::collections::HashSet;

use crate::cvt::eq_nocase;
use crate::tree::Item;

/// A parsed configuration file, with the C parser's "is_read" marks.
pub struct Config {
    pub items: Vec<Item>,
    read: RefCell<HashSet<usize>>,
}

/// The item kinds (config_item_type).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Block,
    Var,
    BoolExpr,
    Set,
}

fn addr(item: &Item) -> usize {
    item as *const Item as usize
}

pub fn kind(item: &Item) -> Kind {
    match item {
        Item::Block { .. } => Kind::Block,
        Item::Affect { .. } => Kind::Var,
        Item::BoolExpr { .. } => Kind::BoolExpr,
        Item::Set { .. } => Kind::Set,
    }
}

pub fn line(item: &Item) -> u32 {
    match item {
        Item::Block { line, .. } | Item::Affect { line, .. } | Item::BoolExpr { line, .. } | Item::Set { line, .. } => *line,
    }
}

/// The name of a block or variable (cfg_item_name); None for expressions.
pub fn name(item: &Item) -> Option<&[u8]> {
    match item {
        Item::Block { name, .. } | Item::Affect { name, .. } => Some(name),
        _ => None,
    }
}

pub fn block_id(item: &Item) -> Option<&[u8]> {
    match item {
        Item::Block { id, .. } => Some(id),
        _ => None,
    }
}

pub fn content(item: &Item) -> &[Item] {
    match item {
        Item::Block { content, .. } => content,
        _ => &[],
    }
}

/// rh_config_ContentType: the kind of a block's first item, if any.
pub fn content_kind(item: &Item) -> Option<Kind> {
    content(item).first().map(kind)
}

/// rh_config_GetKeyValue: (name, value, has extra arguments), or None
/// (then the C error message is NOT_A_VAR).
pub fn key_value(item: &Item) -> Option<(&[u8], &[u8], bool)> {
    match item {
        Item::Affect { name, value, args, .. } => Some((name, value, args.as_ref().map_or(false, |a| !a.is_empty()))),
        _ => None,
    }
}

pub const NOT_A_VAR: &[u8] = b"Expression needs to be interpreted as a <key>=<value> expression, but it is not.";

/// rh_config_GetExtraArgs
pub fn extra_args(item: &Item) -> Vec<Vec<u8>> {
    match item {
        Item::Affect { args, .. } => args.clone().unwrap_or_default(),
        Item::BoolExpr { expr: crate::tree::BoolExpr::Condition { args, .. }, .. } => args.clone().unwrap_or_default(),
        _ => Vec::new(),
    }
}

/// STRNCMP(a, b, MAXSTRLEN): strncasecmp; names are below MAXSTRLEN.
fn same_name(a: &[u8], b: &[u8]) -> bool {
    eq_nocase(a, b)
}

impl Config {
    pub fn new(items: Vec<Item>) -> Config {
        Config { items, read: RefCell::new(HashSet::new()) }
    }

    fn mark(&self, item: &Item) {
        self.read.borrow_mut().insert(addr(item));
    }

    /// rh_config_IsRead
    pub fn is_read(&self, item: &Item) -> bool {
        self.read.borrow().contains(&addr(item))
    }

    /// GetItemFromList: the first item with this name; with `unique`
    /// set, a second one makes `*unique` false and is returned instead.
    fn item_from_list<'a>(&self, list: &'a [Item], wanted: &[u8], unique: &mut bool) -> Option<&'a Item> {
        let mut save: Option<&Item> = None;
        for curr in list {
            let Some(n) = name(curr) else { continue };
            if same_name(n, wanted) {
                if !*unique {
                    self.mark(curr);
                    return Some(curr);
                }
                if save.is_some() {
                    *unique = false;
                    self.mark(curr);
                    return Some(curr);
                }
                save = Some(curr);
            }
        }
        if let Some(s) = save {
            self.mark(s);
        }
        save
    }

    /// Walk "BLOCK::SUB::NAME" from a list (FindItemByName/GetItemByName).
    fn walk<'a>(&self, mut list: &'a [Item], path: &[u8], unique: &mut bool) -> Option<&'a Item> {
        let path = crate::lexer::strncpy(path, crate::lexer::MAXSTRLEN);
        let mut current: &[u8] = &path;
        loop {
            match current.windows(2).position(|w| w == b"::") {
                None => return self.item_from_list(list, current, unique),
                Some(i) => {
                    let block = self.item_from_list(list, &current[..i], unique)?;
                    if kind(block) != Kind::Block {
                        return None;
                    }
                    list = content(block);
                    current = &current[i + 2..];
                }
            }
        }
    }

    /// rh_config_FindItemByName: from the top of the file.
    pub fn find_item_by_name(&self, path: &[u8], unique: &mut bool) -> Option<&Item> {
        if self.items.is_empty() {
            return None;
        }
        self.walk(&self.items, path, unique)
    }

    /// rh_config_GetItemByName: within a block.
    pub fn get_item_by_name<'a>(&self, block: &'a Item, path: &[u8], unique: &mut bool) -> Option<&'a Item> {
        if kind(block) != Kind::Block {
            return None;
        }
        self.walk(content(block), path, unique)
    }

    /// rh_config_CountBlockNames (strcasecmp over the top-level blocks)
    pub fn count_block_names(&self, wanted: &[u8]) -> i32 {
        if self.items.is_empty() {
            return -1;
        }
        self.items.iter().filter(|b| name(b).map_or(false, |n| eq_nocase(n, wanted))).count() as i32
    }
}
