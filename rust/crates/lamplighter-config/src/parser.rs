// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// The algorithm follows the yyparse() that Bison 3.0.4 generated from
// Robinhood's src/cfg_parsing/conf_yacc.y (Copyright (C) 2004-2009
// CEA/DAM, CeCILL-C); the tables are Bison's own (src/tables.rs).

//! The parser: Bison's LALR(1) algorithm on Bison's tables for
//! conf_yacc.y, with that grammar's semantic actions. Because it reads a
//! lookahead token exactly when Bison does, every item gets the line
//! number the C parser gives it, and errors read exactly the same.

use crate::lexer::{cstr, snprintf, Fatal, Lexer, MAXSTRLEN};
use crate::tables::*;
use crate::tree::{BoolExpr, Item, Op, SetExpr};

/// The grammar's tokens, by name (their symbol numbers come from YYTNAME).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Token {
    Eof,
    Error,
    BeginBlock,
    EndBlock,
    EndAffect,
    BeginSubBlock,
    EndSubBlock,
    BeginParenthesis,
    EndParenthesis,
    ValueSeparator,
    Affect,
    Equal,
    Diff,
    Gt,
    GtEq,
    Lt,
    LtEq,
    And,
    Or,
    Not,
    Union,
    Inter,
    Identifier,
    NonIdentifierValue,
    EnvVar,
}

impl Token {
    fn name(self) -> &'static str {
        use Token::*;
        match self {
            Eof => "$end",
            Error => "_ERROR_",
            BeginBlock => "BEGIN_BLOCK",
            EndBlock => "END_BLOCK",
            EndAffect => "END_AFFECT",
            BeginSubBlock => "BEGIN_SUB_BLOCK",
            EndSubBlock => "END_SUB_BLOCK",
            BeginParenthesis => "BEGIN_PARENTHESIS",
            EndParenthesis => "END_PARENTHESIS",
            ValueSeparator => "VALUE_SEPARATOR",
            Affect => "AFFECT",
            Equal => "EQUAL",
            Diff => "DIFF",
            Gt => "GT",
            GtEq => "GT_EQ",
            Lt => "LT",
            LtEq => "LT_EQ",
            And => "AND",
            Or => "OR",
            Not => "NOT",
            Union => "UNION",
            Inter => "INTER",
            Identifier => "IDENTIFIER",
            NonIdentifierValue => "NON_IDENTIFIER_VALUE",
            EnvVar => "ENV_VAR",
        }
    }

    /// The internal symbol number (Bison's YYTRANSLATE of the token).
    fn symbol(self) -> i32 {
        YYTNAME.iter().position(|n| *n == self.name()).expect("token in YYTNAME") as i32
    }
}

/// Semantic values on Bison's value stack (YYSTYPE).
#[derive(Clone, Debug)]
enum Sv {
    None,
    Str(Vec<u8>),
    List(Vec<Item>),
    Item(Item),
    Args(Vec<Vec<u8>>),
}

impl Sv {
    fn str(self) -> Vec<u8> {
        match self {
            Sv::Str(s) => s,
            _ => Vec::new(),
        }
    }
    fn list(self) -> Vec<Item> {
        match self {
            Sv::List(l) => l,
            _ => Vec::new(),
        }
    }
    fn item(self) -> Option<Item> {
        match self {
            Sv::Item(i) => Some(i),
            _ => None,
        }
    }
    fn args(self) -> Vec<Vec<u8>> {
        match self {
            Sv::Args(a) => a,
            _ => Vec::new(),
        }
    }
}

/// How a parse ends.
pub enum Outcome {
    Tree(Vec<Item>),
    /// The message the C parser leaves in extern_errormsg.
    Error(Vec<u8>),
    Fatal(Fatal),
}

/// yytnamerr: a symbol name, without its double quotes when safe.
fn tname(n: &str) -> Vec<u8> {
    let b = n.as_bytes();
    if b.first() == Some(&b'"') {
        let mut out = Vec::new();
        let mut i = 1;
        loop {
            match b.get(i) {
                Some(b'\'') | Some(b',') | None => break,
                Some(b'\\') => {
                    i += 1;
                    if b.get(i) != Some(&b'\\') {
                        break;
                    }
                    out.push(b'\\');
                }
                Some(b'"') => return out,
                Some(&c) => out.push(c),
            }
            i += 1;
        }
    }
    b.to_vec()
}

fn pact_is_default(n: i32) -> bool {
    n == YYPACT_NINF
}

fn table_is_error(n: i32) -> bool {
    n == YYTABLE_NINF && false // Bison 3.0.4: yytable_value_is_error is 0 for this grammar
}

/// yysyntax_error: "syntax error, unexpected X[, expecting A or B ...]".
fn syntax_error(state: i32, token: Option<i32>) -> Vec<u8> {
    let Some(tok) = token else { return b"syntax error".to_vec() };
    let mut args: Vec<Vec<u8>> = vec![tname(YYTNAME[tok as usize])];
    let n = YYPACT[state as usize];
    if !pact_is_default(n) {
        let begin = if n < 0 { -n } else { 0 };
        let checklim = YYLAST - n + 1;
        let end = checklim.min(YYNTOKENS);
        for x in begin..end {
            if YYCHECK[(x + n) as usize] == x && x != YYTERROR && !table_is_error(YYTABLE[(x + n) as usize]) {
                if args.len() == 5 {
                    args.truncate(1);
                    break;
                }
                args.push(tname(YYTNAME[x as usize]));
            }
        }
    }
    let mut out = b"syntax error, unexpected ".to_vec();
    out.extend_from_slice(&args[0]);
    for (i, a) in args.iter().enumerate().skip(1) {
        out.extend_from_slice(if i == 1 { b", expecting " } else { b" or " });
        out.extend_from_slice(a);
    }
    out
}

/// yyerror in conf_yacc.y: the message with the scanner's error, the
/// current token's text, line and file; cut to 1023 bytes ("..." last).
fn yyerror(lx: &Lexer, s: &[u8]) -> Vec<u8> {
    let local = cstr(&lx.local_errormsg);
    let text = cstr(&lx.yytext);
    let file = cstr(&lx.current_file);
    let mut m = Vec::new();
    if !local.is_empty() && !s.is_empty() {
        m.extend_from_slice(local);
        m.extend_from_slice(b" (");
        m.extend_from_slice(s);
        m.extend_from_slice(b")");
    } else if !local.is_empty() {
        m.extend_from_slice(local);
    } else if !s.is_empty() {
        m.extend_from_slice(s);
    } else {
        m.extend_from_slice(b"Syntax error");
    }
    m.extend_from_slice(b" at '");
    m.extend_from_slice(text);
    m.extend_from_slice(format!("' line {} in '", lx.yylineno).as_bytes());
    m.extend_from_slice(file);
    m.push(b'\'');
    let full = cstr(&m).len();
    let mut out = snprintf(m, 1024);
    if full >= 1024 {
        out.truncate(1020);
        out.extend_from_slice(b"...");
    }
    out
}

fn key_value(name: Vec<u8>, op: Op, value: Vec<u8>, line: u32) -> Item {
    let s = |v: Vec<u8>| crate::lexer::strncpy(&v, MAXSTRLEN);
    Item::BoolExpr { expr: BoolExpr::Condition { name: s(name), op, value: s(value), args: None }, line }
}

fn expr_of(item: Option<Item>) -> Option<BoolExpr> {
    match item {
        Some(Item::BoolExpr { expr, .. }) => Some(expr),
        _ => None,
    }
}

fn set_of(item: Option<Item>) -> Option<SetExpr> {
    match item {
        Some(Item::Set { set, .. }) => Some(set),
        _ => None,
    }
}

/// The semantic action of rule `rule`, on the values of its right-hand
/// side (`rhs[0]` is $1). Rule numbers are Bison's, in grammar order.
fn action(rule: i32, mut rhs: Vec<Sv>, lx: &mut Lexer) -> Sv {
    let line = lx.yylineno;
    let empty = rhs.is_empty();
    let mut take = |i: usize| std::mem::replace(&mut rhs[i], Sv::None);
    match rule {
        // program: listblock
        2 => take(0),
        // listblock: block listblock | listitems: definition listitems
        3 | 7 => {
            let item = take(0).item();
            let mut list = take(1).list();
            if let Some(item) = item {
                list.insert(0, item);
            }
            Sv::List(list)
        }
        // listblock: (empty) | listitems: (empty)
        4 | 8 => Sv::List(Vec::new()),
        // block: IDENTIFIER IDENTIFIER { listitems } | IDENTIFIER { listitems }
        5 | 6 => {
            let (name, id, content) = if rule == 5 {
                (take(0).str(), take(1).str(), take(3).list())
            } else {
                (take(0).str(), Vec::new(), take(2).list())
            };
            Sv::Item(Item::Block { name: cstr(&name).to_vec(), id: cstr(&id).to_vec(), content, line })
        }
        // definition: extended_affect | subblock
        9 | 10 => take(0),
        // value: IDENTIFIER | NON_IDENTIFIER_VALUE
        11 | 12 => take(0),
        // value: ENV_VAR
        13 => {
            let v = take(0).str();
            Sv::Str(lx.resolve_var(&v))
        }
        // affect: IDENTIFIER AFFECT value
        14 => {
            let s = |v: Vec<u8>| crate::lexer::strncpy(&v, MAXSTRLEN);
            Sv::Item(Item::Affect { name: s(take(0).str()), value: s(take(2).str()), args: None, line })
        }
        // extended_affect: affect ( arglist ) ; | affect ;
        15 | 16 => {
            let mut item = take(0);
            if rule == 15 {
                if let Sv::Item(Item::Affect { args, .. }) = &mut item {
                    *args = Some(take(2).args());
                }
            }
            item
        }
        // key_value: IDENTIFIER (== != > >= < <=) value
        17..=22 => {
            let op = [Op::Equal, Op::Diff, Op::Gt, Op::GtEq, Op::Lt, Op::LtEq][(rule - 17) as usize];
            Sv::Item(key_value(take(0).str(), op, take(2).str(), line))
        }
        // arglist: arglist , value
        23 => {
            let mut a = take(0).args();
            a.push(cstr(&take(2).str()).to_vec());
            Sv::Args(a)
        }
        // arglist: value
        24 => Sv::Args(vec![cstr(&take(0).str()).to_vec()]),
        // extended_key_value: key_value ( arglist )
        25 => {
            let mut item = take(0);
            if let Sv::Item(Item::BoolExpr { expr: BoolExpr::Condition { args, .. }, .. }) = &mut item {
                *args = Some(take(2).args());
            }
            item
        }
        // extended_key_value: key_value
        26 => take(0),
        // extended_key_value: IDENTIFIER ( value )
        27 => Sv::Item(key_value(take(0).str(), Op::Cmd, take(2).str(), line)),
        // expression: extended_key_value
        28 => take(0),
        // expression: NOT extended_key_value | NOT ( expression )
        29 | 30 => {
            let inner = expr_of(take(if rule == 29 { 1 } else { 2 }).item());
            match inner {
                Some(e) => Sv::Item(Item::BoolExpr { expr: BoolExpr::Not(Box::new(e)), line }),
                None => Sv::None,
            }
        }
        // expression: ( expression )
        31 => take(1),
        // expression: expression AND|OR expression
        32 | 33 => match (expr_of(take(0).item()), expr_of(take(2).item())) {
            (Some(a), Some(b)) => {
                let e = if rule == 32 { BoolExpr::And(Box::new(a), Box::new(b)) } else { BoolExpr::Or(Box::new(a), Box::new(b)) };
                Sv::Item(Item::BoolExpr { expr: e, line })
            }
            _ => Sv::None,
        },
        // set: ( set )
        34 => take(1),
        // set: NOT set
        35 => match set_of(take(1).item()) {
            Some(s) => Sv::Item(Item::Set { set: SetExpr::Not(Box::new(s)), line }),
            None => Sv::None,
        },
        // set: set UNION|INTER set
        36 | 37 => match (set_of(take(0).item()), set_of(take(2).item())) {
            (Some(a), Some(b)) => {
                let s = if rule == 36 { SetExpr::Union(Box::new(a), Box::new(b)) } else { SetExpr::Inter(Box::new(a), Box::new(b)) };
                Sv::Item(Item::Set { set: s, line })
            }
            _ => Sv::None,
        },
        // set: IDENTIFIER
        38 => Sv::Item(Item::Set { set: SetExpr::Singleton(crate::lexer::strncpy(&take(0).str(), MAXSTRLEN)), line }),
        // subblock: name id { listitems } | name id { expression } |
        //           name { listitems } | name { expression } | name { set }
        39..=43 => {
            let named = rule == 39 || rule == 40;
            let name = take(0).str();
            let id = if named { take(1).str() } else { Vec::new() };
            let body = take(if named { 3 } else { 2 });
            let content = match body {
                Sv::List(l) => l,
                Sv::Item(i) => vec![i],
                _ => Vec::new(),
            };
            Sv::Item(Item::Block { name: cstr(&name).to_vec(), id: cstr(&id).to_vec(), content, line })
        }
        _ => {
            // rules without an action: $$ = $1
            if empty { Sv::None } else { take(0) }
        }
    }
}

/// yyparse(): parse the scanner's tokens into the item list.
pub fn parse(lx: &mut Lexer) -> Outcome {
    let mut stacksize: usize = YYINITDEPTH as usize;
    let mut states: Vec<i32> = vec![0];
    let mut values: Vec<Sv> = vec![Sv::None];
    let mut lookahead: Option<(i32, Sv)> = None; // (symbol, value); None = YYEMPTY
    let mut result: Vec<Item> = Vec::new();

    'newstate: loop {
        let state = *states.last().unwrap();
        // stack growth, as yysetstate does it
        if states.len() >= stacksize {
            if YYMAXDEPTH as usize <= stacksize {
                return Outcome::Error(yyerror(lx, b"memory exhausted"));
            }
            stacksize = (stacksize * 2).min(YYMAXDEPTH as usize);
        }
        if state == YYFINAL {
            return Outcome::Tree(result);
        }
        // yybackup
        let mut n = YYPACT[state as usize];
        let rule;
        'decide: {
            if !pact_is_default(n) {
                if lookahead.is_none() {
                    match lx.next() {
                        Err(f) => return Outcome::Fatal(f),
                        Ok(lexeme) => {
                            let v = if lexeme.value.is_empty() && !matches!(lexeme.token, Token::Identifier | Token::NonIdentifierValue | Token::EnvVar) {
                                Sv::None
                            } else {
                                Sv::Str(lexeme.value)
                            };
                            lookahead = Some((lexeme.token.symbol(), v));
                        }
                    }
                }
                let tok = lookahead.as_ref().unwrap().0;
                n += tok;
                if !(n < 0 || YYLAST < n || YYCHECK[n as usize] != tok) {
                    let t = YYTABLE[n as usize];
                    if t <= 0 {
                        if table_is_error(t) {
                            return Outcome::Error(yyerror(lx, &syntax_error(state, Some(tok))));
                        }
                        rule = -t;
                        break 'decide;
                    }
                    // shift
                    let (_, v) = lookahead.take().unwrap();
                    states.push(t);
                    values.push(v);
                    continue 'newstate;
                }
            }
            // yydefault
            let r = YYDEFACT[state as usize];
            if r == 0 {
                let tok = lookahead.as_ref().map(|(t, _)| *t);
                return Outcome::Error(yyerror(lx, &syntax_error(state, tok)));
            }
            rule = r;
        }
        // yyreduce
        let len = YYR2[rule as usize] as usize;
        let rhs: Vec<Sv> = values.split_off(values.len() - len);
        states.truncate(states.len() - len);
        let v = action(rule, rhs, lx);
        if rule == 2 {
            if let Sv::List(l) = &v {
                result = l.clone();
            }
        }
        let lhs = YYR1[rule as usize] - YYNTOKENS;
        let top = *states.last().unwrap();
        let i = YYPGOTO[lhs as usize] + top;
        let next = if (0..=YYLAST).contains(&i) && YYCHECK[i as usize] == top {
            YYTABLE[i as usize]
        } else {
            YYDEFGOTO[lhs as usize]
        };
        states.push(next);
        values.push(v);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The rule numbers in `action` are Bison's: check each rule's
    /// left-hand side and length against the tables.
    #[test]
    fn rule_numbers_match_the_tables() {
        let rules: [(i32, &str, i32); 43] = [
            (1, "$accept", 2), (2, "program", 1), (3, "listblock", 2), (4, "listblock", 0),
            (5, "block", 5), (6, "block", 4), (7, "listitems", 2), (8, "listitems", 0),
            (9, "definition", 1), (10, "definition", 1), (11, "value", 1), (12, "value", 1),
            (13, "value", 1), (14, "affect", 3), (15, "extended_affect", 5), (16, "extended_affect", 2),
            (17, "key_value", 3), (18, "key_value", 3), (19, "key_value", 3), (20, "key_value", 3),
            (21, "key_value", 3), (22, "key_value", 3), (23, "arglist", 3), (24, "arglist", 1),
            (25, "extended_key_value", 4), (26, "extended_key_value", 1), (27, "extended_key_value", 4),
            (28, "expression", 1), (29, "expression", 2), (30, "expression", 4), (31, "expression", 3),
            (32, "expression", 3), (33, "expression", 3), (34, "set", 3), (35, "set", 2),
            (36, "set", 3), (37, "set", 3), (38, "set", 1), (39, "subblock", 5), (40, "subblock", 5),
            (41, "subblock", 4), (42, "subblock", 4), (43, "subblock", 4),
        ];
        assert_eq!(YYNRULES, 43);
        for (r, lhs, len) in rules {
            assert_eq!(YYTNAME[YYR1[r as usize] as usize], lhs, "rule {r}");
            assert_eq!(YYR2[r as usize], len, "rule {r}");
        }
    }

    #[test]
    fn every_token_has_a_symbol() {
        use Token::*;
        for t in [Eof, Error, BeginBlock, EndBlock, EndAffect, BeginSubBlock, EndSubBlock, BeginParenthesis,
                  EndParenthesis, ValueSeparator, Affect, Equal, Diff, Gt, GtEq, Lt, LtEq, And, Or, Not,
                  Union, Inter, Identifier, NonIdentifierValue, EnvVar] {
            let s = t.symbol();
            assert!(s >= 0 && s < YYNTOKENS);
        }
    }
}
