// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// A faithful emulation of Robinhood's flex scanner, src/cfg_parsing/conf_lex.l
// (Copyright (C) 2004-2009 CEA/DAM, CeCILL-C), built with `flex -i`.

//! The scanner. It reproduces flex's behaviour, not just its tokens:
//!
//! - **Rule selection:** at each position, every rule active in the
//!   current start condition is tried; the longest match wins, and on a
//!   tie the rule listed first in conf_lex.l. Rules for a trailing `$`
//!   (end of line) count the newline in that comparison but leave it in
//!   the input, and do not match at the end of a file without a newline.
//! - **Start conditions** are inclusive (`%START`): rules without a
//!   condition (spaces, newlines, the "unexpected" error) apply in every
//!   state, after the state's own rules on ties.
//! - **`-i`:** keywords and `%include` match case-insensitively.
//! - **C strings:** values are copied the way the C code copies them
//!   (`rh_strncpy` to 1023 bytes, stopping at a NUL byte).
//! - **`%include`:** nesting limit, environment variables, relative paths,
//!   loop detection and line numbers, as in the C scanner.

use crate::parser::Token;

pub const MAXSTRLEN: usize = 1024;
const MAX_INCLUDE_DEPTH: i32 = 10;
const ERRLEN: usize = 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum State {
    YyInit,
    InBloc,
    String1,
    String2,
    Esc1,
    Include,
    InclString,
    InclEsc,
}

/// Fatal scanner errors: flex's YY_FATAL_ERROR ("input in flex scanner
/// failed") when a file cannot be read, as the C program exits.
#[derive(Debug)]
pub struct Fatal(pub String);

struct Buffer {
    data: Vec<u8>,
    pos: usize,
}

pub struct Lexer {
    state: State,
    buffers: Vec<Buffer>,
    include_stack_index: i32,
    lines_stack: [u32; MAX_INCLUDE_DEPTH as usize],
    files_stack: Vec<Vec<u8>>,
    include_prev_state: State,
    accolades: i32,
    parenthesis: i32,
    parsed: Vec<u8>,
    pub yylineno: u32,
    pub current_file: Vec<u8>,
    /// The text of the last rule matched (yytext).
    pub yytext: Vec<u8>,
    /// The scanner's error message (local_errormsg in conf_yacc.y).
    pub local_errormsg: Vec<u8>,
    /// Messages the C code prints to stderr (undefined variables).
    pub warnings: Vec<Vec<u8>>,
}

/// The C string in `s`: everything before the first NUL byte.
pub fn cstr(s: &[u8]) -> &[u8] {
    match s.iter().position(|&c| c == 0) {
        Some(i) => &s[..i],
        None => s,
    }
}

/// `rh_strncpy(dst, src, size)`: a C string cut to `size - 1` bytes.
pub fn strncpy(src: &[u8], size: usize) -> Vec<u8> {
    let s = cstr(src);
    s[..s.len().min(size - 1)].to_vec()
}

/// `snprintf(buf, size, ...)` of already formatted bytes: a C string cut
/// to `size - 1` bytes.
pub fn snprintf(formatted: Vec<u8>, size: usize) -> Vec<u8> {
    let mut s = cstr(&formatted).to_vec();
    s.truncate(size - 1);
    s
}

/// The C library's getenv(), exactly as the C code calls it.
fn getenv(name: &[u8]) -> Option<Vec<u8>> {
    let name = std::ffi::CString::new(cstr(name)).ok()?;
    // SAFETY: name is a valid C string; the result is copied at once.
    let p = unsafe { libc::getenv(name.as_ptr()) };
    if p.is_null() {
        return None;
    }
    Some(unsafe { std::ffi::CStr::from_ptr(p) }.to_bytes().to_vec())
}

/// POSIX dirname(), as glibc implements it.
fn dirname(path: &[u8]) -> Vec<u8> {
    let mut end = path.len();
    while end > 1 && path[end - 1] == b'/' {
        end -= 1;
    }
    let Some(slash) = path[..end].iter().rposition(|&c| c == b'/') else {
        return b".".to_vec();
    };
    let mut d = slash;
    while d > 0 && path[d - 1] == b'/' {
        d -= 1;
    }
    if d == 0 {
        // glibc keeps a leading "//" (POSIX allows it to mean something else)
        return if slash == 1 && path.len() > 2 && path[2] != b'/' { b"//".to_vec() } else { b"/".to_vec() };
    }
    path[..d].to_vec()
}

fn strerror(errno: i32) -> Vec<u8> {
    // SAFETY: strerror returns a pointer to a static or thread-local string.
    let p = unsafe { libc::strerror(errno) };
    if p.is_null() {
        return format!("Unknown error {errno}").into_bytes();
    }
    unsafe { std::ffi::CStr::from_ptr(p) }.to_bytes().to_vec()
}

fn is_letter(c: u8) -> bool {
    c.is_ascii_alphabetic() || c == b'_' || c == b'.'
}

fn is_ident_char(c: u8) -> bool {
    c.is_ascii_alphanumeric() || c == b'_' || c == b'.' || c == b'-'
}

fn is_val_char(c: u8) -> bool {
    !matches!(c, b' ' | b'\t' | b'\r' | b'\n' | 0x0c | b'"' | b'\'' | b'#' | b'(' | b')' | b',' | b';' | b'=' | b'<' | b'>' | b'!' | b'{' | b'}')
}

fn is_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\r' | 0x0c)
}

/// A literal, case-insensitive (flex -i).
fn lit(rest: &[u8], word: &[u8]) -> Option<usize> {
    (rest.len() >= word.len() && rest[..word.len()].eq_ignore_ascii_case(word)).then_some(word.len())
}

fn identifier(rest: &[u8]) -> Option<usize> {
    if rest.first().copied().map_or(false, is_letter) {
        Some(1 + rest[1..].iter().take_while(|&&c| is_ident_char(c)).count())
    } else {
        None
    }
}

/// `#.*$` or `//.*$`: (text length, length including the newline).
fn comment(rest: &[u8], start: &[u8]) -> Option<(usize, usize)> {
    if !rest.starts_with(start) {
        return None;
    }
    let n = rest.iter().position(|&c| c == b'\n')?; // `$`: needs the newline
    Some((n, n + 1))
}

/// The rules of conf_lex.l, in file order. Each yields
/// (length compared, length of yytext) when it matches.
fn rule_match(rule: usize, st: State, rest: &[u8]) -> Option<(usize, usize)> {
    use State::*;
    let one = |p: bool| p.then_some((1, 1));
    let first = rest.first().copied();
    let m = |o: Option<usize>| o.map(|n| (n, n));
    match rule {
        0 if st == YyInit => m(lit(rest, b"%include")),
        1 if st == InBloc => m(lit(rest, b"%include")),
        2 if st == Include => one(first == Some(b'"')),
        3 if st == InclString => one(first == Some(b'\\')),
        4 if st == InclString => one(first == Some(b'"')),
        5 if st == InclString => one(first == Some(b'\n')),
        6 if st == InclString => one(first.map_or(false, |c| c != b'\n')),
        7 if st == InclEsc => one(first == Some(b'\n')),
        8 if st == InclEsc => one(first.map_or(false, |c| c != b'\n')),
        9 if st == YyInit => m(identifier(rest)),
        10 if st == YyInit => one(first == Some(b'{')),
        11 if st == InBloc => one(first == Some(b'(')),
        12 if st == InBloc => one(first == Some(b',')),
        13 if st == InBloc => one(first == Some(b')')),
        14 if st == InBloc => m(lit(rest, b"not")),
        15 if st == InBloc => m(lit(rest, b"and")),
        16 if st == InBloc => m(lit(rest, b"or")),
        17 if st == InBloc => m(lit(rest, b"union")),
        18 if st == InBloc => m(lit(rest, b"inter")),
        19 if st == InBloc => {
            if first == Some(b'$') { m(identifier(&rest[1..]).map(|n| n + 1)) } else { None }
        }
        20 if st == InBloc => m(identifier(rest)),
        21 if st == InBloc => one(first == Some(b'}')),
        22 if st == InBloc => m(lit(rest, b"==")),
        23 if st == InBloc => m(lit(rest, b">")),
        24 if st == InBloc => m(lit(rest, b">=")),
        25 if st == InBloc => m(lit(rest, b"<")),
        26 if st == InBloc => m(lit(rest, b"<=")),
        27 if st == InBloc => m(lit(rest, b"<>")),
        28 if st == InBloc => m(lit(rest, b"!=")),
        29 if st == InBloc => m(lit(rest, b"=")),
        30 if st == InBloc => one(first == Some(b'{')),
        31 if st == InBloc => one(first == Some(b'"')),
        32 if st == InBloc => one(first == Some(b'\'')),
        33 if st == InBloc => {
            let n = rest.iter().take_while(|&&c| is_val_char(c)).count();
            (n > 0).then_some((n, n))
        }
        34 if st == InBloc => one(first == Some(b';')),
        35 if st == String1 => one(first == Some(b'\\')),
        36 if st == String1 => one(first == Some(b'"')),
        37 if st == String1 => one(first == Some(b'\n')),
        38 if st == String1 => one(first.map_or(false, |c| c != b'\n')),
        39 if st == Esc1 => one(first == Some(b'\n')),
        40 if st == Esc1 => one(first.map_or(false, |c| c != b'\n')),
        41 if st == String2 => one(first == Some(b'\'')),
        42 if st == String2 => one(first == Some(b'\n')),
        43 if st == String2 => one(first.map_or(false, |c| c != b'\n')),
        44 if st == InBloc => comment(rest, b"#").map(|(t, c)| (c, t)),
        45 if st == YyInit => comment(rest, b"#").map(|(t, c)| (c, t)),
        46 if st == InBloc => comment(rest, b"//").map(|(t, c)| (c, t)),
        47 if st == YyInit => comment(rest, b"//").map(|(t, c)| (c, t)),
        48 => one(first.map_or(false, is_space)),
        49 => one(first == Some(b'\n')),
        50 => one(first.map_or(false, |c| c != b'\n')),
        _ => None,
    }
}

const NRULES: usize = 51;

/// What the scanner hands the parser: a token, and the string value of
/// IDENTIFIER, NON_IDENTIFIER_VALUE and ENV_VAR.
pub struct Lexeme {
    pub token: Token,
    pub value: Vec<u8>,
}

impl Lexer {
    pub fn new(path: &[u8], data: Vec<u8>) -> Lexer {
        Lexer {
            state: State::YyInit,
            buffers: vec![Buffer { data, pos: 0 }],
            include_stack_index: 0,
            lines_stack: [0; MAX_INCLUDE_DEPTH as usize],
            files_stack: vec![Vec::new(); MAX_INCLUDE_DEPTH as usize],
            include_prev_state: State::YyInit,
            accolades: 0,
            parenthesis: 0,
            parsed: Vec::new(),
            yylineno: 1,
            current_file: path.to_vec(),
            yytext: Vec::new(),
            local_errormsg: Vec::new(),
            warnings: Vec::new(),
        }
    }

    fn set_error(&mut self, msg: Vec<u8>) -> Lexeme {
        let msg = snprintf(msg, ERRLEN);
        self.local_errormsg = strncpy(&msg, 1024);
        Lexeme { token: Token::Error, value: Vec::new() }
    }

    fn where_(&self) -> Vec<u8> {
        let mut s = b"in \"".to_vec();
        s.extend_from_slice(cstr(&self.current_file));
        s.extend_from_slice(format!("\", line {}: ", self.yylineno).as_bytes());
        s
    }

    fn tok(&self, token: Token) -> Result<Option<Lexeme>, Fatal> {
        Ok(Some(Lexeme { token, value: Vec::new() }))
    }

    fn val(&self, token: Token, v: &[u8]) -> Result<Option<Lexeme>, Fatal> {
        Ok(Some(Lexeme { token, value: strncpy(v, MAXSTRLEN) }))
    }

    /// The next token (yylex). `Ok(token)`, or `Err` for a fatal error.
    pub fn next(&mut self) -> Result<Lexeme, Fatal> {
        loop {
            let buf = self.buffers.last().expect("a buffer");
            let rest = &buf.data[buf.pos..];
            if rest.is_empty() {
                // <<EOF>>
                self.yytext.clear();
                self.include_stack_index -= 1;
                if self.include_stack_index < 0 {
                    return Ok(Lexeme { token: Token::Eof, value: Vec::new() });
                }
                self.buffers.pop();
                let i = self.include_stack_index as usize;
                self.yylineno = self.lines_stack[i];
                self.current_file = self.files_stack[i].clone();
                continue;
            }
            // flex: longest match, then the earliest rule
            let mut best: Option<(usize, usize, usize)> = None; // (rule, compared, text)
            for r in 0..NRULES {
                if let Some((cmp, txt)) = rule_match(r, self.state, rest) {
                    if best.map_or(true, |(_, b, _)| cmp > b) {
                        best = Some((r, cmp, txt));
                    }
                }
            }
            let (rule, _, len) = best.expect("rule 50 or 49 always matches");
            let text = rest[..len].to_vec();
            self.buffers.last_mut().unwrap().pos += len;
            self.yytext = text.clone();
            if let Some(lexeme) = self.action(rule, &text)? {
                return Ok(lexeme);
            }
        }
    }

    fn action(&mut self, rule: usize, text: &[u8]) -> Result<Option<Lexeme>, Fatal> {
        use State::*;
        match rule {
            0 => {
                self.state = Include;
                self.include_prev_state = YyInit;
            }
            1 => {
                self.state = Include;
                self.include_prev_state = InBloc;
            }
            2 => {
                self.state = InclString;
                self.parsed.clear();
            }
            3 => self.state = InclEsc,
            4 => return self.include(),
            5 => {
                let mut m = self.where_();
                m.extend_from_slice(b"missing closing quote.");
                self.yylineno += 1;
                return Ok(Some(self.set_error(m)));
            }
            6 | 38 | 43 => self.parsed.extend_from_slice(cstr(text)),
            7 => {
                self.state = InclString;
                self.yylineno += 1;
            }
            8 => {
                self.parsed.extend_from_slice(cstr(text));
                self.state = InclString;
            }
            9 | 20 => return self.val(Token::Identifier, text),
            10 => {
                self.state = InBloc;
                self.accolades += 1;
                return self.tok(Token::BeginBlock);
            }
            11 => {
                self.parenthesis += 1;
                return self.tok(Token::BeginParenthesis);
            }
            12 => return self.tok(Token::ValueSeparator),
            13 => {
                self.state = InBloc;
                if self.parenthesis <= 0 {
                    let mut m = self.where_();
                    m.extend_from_slice(b"'");
                    m.push(text[0]);
                    m.extend_from_slice(b"' too much closing parenthesis");
                    return Ok(Some(self.set_error(m)));
                }
                self.parenthesis -= 1;
                return self.tok(Token::EndParenthesis);
            }
            14 => return self.tok(Token::Not),
            15 => return self.tok(Token::And),
            16 => return self.tok(Token::Or),
            17 => return self.tok(Token::Union),
            18 => return self.tok(Token::Inter),
            19 => return self.val(Token::EnvVar, text),
            21 => {
                if self.accolades <= 0 {
                    let mut m = self.where_();
                    m.extend_from_slice(b"'");
                    m.push(text[0]);
                    m.extend_from_slice(b"' closing bracket outside a block");
                    return Ok(Some(self.set_error(m)));
                }
                self.accolades -= 1;
                if self.accolades == 0 {
                    self.state = YyInit;
                    return self.tok(Token::EndBlock);
                }
                self.state = InBloc;
                return self.tok(Token::EndSubBlock);
            }
            22 => return self.tok(Token::Equal),
            23 => return self.tok(Token::Gt),
            24 => return self.tok(Token::GtEq),
            25 => return self.tok(Token::Lt),
            26 => return self.tok(Token::LtEq),
            27 | 28 => return self.tok(Token::Diff),
            29 => return self.tok(Token::Affect),
            30 => {
                self.state = InBloc;
                self.accolades += 1;
                return self.tok(Token::BeginSubBlock);
            }
            31 => {
                self.state = String1;
                self.parsed.clear();
            }
            32 => {
                self.state = String2;
                self.parsed.clear();
            }
            33 => return self.val(Token::NonIdentifierValue, text),
            34 => return self.tok(Token::EndAffect),
            35 => self.state = Esc1,
            36 | 41 => {
                self.state = InBloc;
                let v = self.parsed.clone();
                return self.val(Token::NonIdentifierValue, &v);
            }
            37 | 42 => {
                let mut m = self.where_();
                m.extend_from_slice(if rule == 37 { b"missing closing quote." } else { b"closing quote missing." });
                self.yylineno += 1;
                return Ok(Some(self.set_error(m)));
            }
            39 => {
                self.state = String1;
                self.yylineno += 1;
            }
            40 => {
                self.parsed.extend_from_slice(cstr(text));
                self.state = String1;
            }
            44..=48 => {}
            49 => self.yylineno += 1,
            50 => {
                let mut m = self.where_();
                m.extend_from_slice(b"'");
                m.push(text[0]);
                m.extend_from_slice(b"' unexpected");
                return Ok(Some(self.set_error(m)));
            }
            _ => unreachable!("rule {rule}"),
        }
        Ok(None)
    }

    /// The end of `%include "file"`: open the file and scan it next.
    fn include(&mut self) -> Result<Option<Lexeme>, Fatal> {
        if self.include_stack_index >= MAX_INCLUDE_DEPTH {
            let mut m = self.where_();
            m.extend_from_slice(b"includes nested too deeply");
            return Ok(Some(self.set_error(m)));
        }
        if self.parsed.first() == Some(&b'$') {
            match getenv(&self.parsed[1..]) {
                None => {
                    let mut m = self.where_();
                    m.extend_from_slice(b"environment variable '");
                    m.extend_from_slice(cstr(&self.parsed[1..]));
                    m.extend_from_slice(b"' is not set");
                    return Ok(Some(self.set_error(m)));
                }
                Some(v) if v.len() >= MAXSTRLEN => {
                    let mut m = self.where_();
                    m.extend_from_slice(b"file name too long in include statement");
                    return Ok(Some(self.set_error(m)));
                }
                Some(v) => self.parsed = v,
            }
        }
        let i = self.include_stack_index as usize;
        self.lines_stack[i] = self.yylineno;
        self.files_stack[i] = cstr(&self.current_file).to_vec();

        let name = cstr(&self.parsed).to_vec();
        let new_path = if name.first() == Some(&b'/') || !cstr(&self.current_file).contains(&b'/') {
            name
        } else {
            let mut p = dirname(cstr(&self.current_file));
            p.push(b'/');
            p.extend_from_slice(&name);
            p
        };
        for j in 0..=i {
            if self.files_stack[j] == new_path {
                let mut m = self.where_();
                m.extend_from_slice(b"include loop detected: \"");
                m.extend_from_slice(&new_path);
                m.extend_from_slice(b"\" already parsed");
                return Ok(Some(self.set_error(m)));
            }
        }
        self.include_stack_index += 1;

        use std::os::unix::ffi::OsStrExt;
        let path = std::path::Path::new(std::ffi::OsStr::from_bytes(&new_path));
        match std::fs::File::open(path) {
            Err(e) => {
                let errno = e.raw_os_error().unwrap_or(0);
                let mut m = self.where_();
                m.extend_from_slice(format!("error {errno} opening file \"").as_bytes());
                m.extend_from_slice(&new_path);
                m.extend_from_slice(b"\": ");
                m.extend_from_slice(&strerror(errno));
                Ok(Some(self.set_error(m)))
            }
            Ok(mut f) => {
                use std::io::Read;
                let mut data = Vec::new();
                // flex's YY_INPUT: a read error (a directory) is fatal
                if f.read_to_end(&mut data).is_err() {
                    return Err(Fatal("input in flex scanner failed".into()));
                }
                self.yylineno = 1;
                self.current_file = new_path;
                self.buffers.push(Buffer { data, pos: 0 });
                self.state = self.include_prev_state;
                Ok(None)
            }
        }
    }

    /// `rh_config_resolv_var`: the value of `$NAME`, or "" with a warning.
    pub fn resolve_var(&mut self, var: &[u8]) -> Vec<u8> {
        let name = cstr(var).get(1..).unwrap_or(&[]);
        match getenv(name) {
            Some(v) => strncpy(&v, MAXSTRLEN),
            None => {
                let mut w = b"WARNING: environment variable ".to_vec();
                w.extend_from_slice(name);
                w.extend_from_slice(b" is not defined.\n");
                self.warnings.push(w);
                Vec::new()
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dirname_like_glibc() {
        for (p, d) in [("/a/b", "/a"), ("/a/b/", "/a"), ("a", "."), ("/", "/"), ("/a", "/"),
                       ("a/b", "a"), ("a//b", "a"), ("//a", "//"), ("///a", "/"), ("", ".")] {
            assert_eq!(dirname(p.as_bytes()), d.as_bytes(), "{p}");
        }
    }

    #[test]
    fn c_strings() {
        assert_eq!(strncpy(b"ab\0cd", 1024), b"ab");
        assert_eq!(strncpy(&[b'x'; 2000], 1024).len(), 1023);
    }
}
