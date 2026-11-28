// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// Translated from Robinhood's src/common/rbh_logs.c
// (Copyright (C) CEA/DAM, CeCILL-C).

//! DisplayLog as it behaves before the logs are initialised (while the
//! configuration loads): lines go to stderr as
//! "YYYY/MM/DD HH:MM:SS [pid/thread] tag | message", with "lamplighter"
//! before the brackets when Log::log_procname is set, non-printable
//! characters replaced by '?', and lines cut at 2048 bytes.

use std::io::Write;

pub const LVL_CRIT: i32 = 0;
pub const LVL_MAJOR: i32 = 2;
pub const LVL_EVENT: i32 = 5;
pub const LVL_VERB: i32 = 10;
pub const LVL_DEBUG: i32 = 50;
pub const LVL_FULL: i32 = 100;

const MAX_LINE_LEN: usize = 2048;

/// The part of log_config that decides how and whether a line prints.
#[derive(Clone, Debug)]
pub struct LogState {
    pub debug_level: i32,
    pub log_process: bool,
}

impl Default for LogState {
    /// log_config's initial value in rbh_logs.c
    fn default() -> LogState {
        LogState { debug_level: LVL_EVENT, log_process: false }
    }
}

fn clean(line: &mut [u8]) {
    for c in line.iter_mut() {
        // isprint() in the C locale, on a signed char
        if *c != b'\n' && *c != b'\t' && !(0x20..0x7f).contains(c) {
            *c = b'?';
        }
    }
}

/// The C code's thread index for the main (first) thread.
const THREAD_INDEX: u32 = 1;

/// Format one line as display_line_log() does before initialisation.
pub fn format_line(state: &LogState, tag: &[u8], msg: &[u8]) -> Vec<u8> {
    // SAFETY: time and localtime_r with valid pointers.
    let (y, mo, d, h, mi, s) = unsafe {
        let now = libc::time(std::ptr::null_mut());
        let mut tm: libc::tm = std::mem::zeroed();
        libc::localtime_r(&now, &mut tm);
        (1900 + tm.tm_year, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec)
    };
    let mut head = format!("{y:04}/{mo:02}/{d:02} {h:02}:{mi:02}:{s:02} {}[{}/{}] ",
        if state.log_process { "lamplighter" } else { "" }, std::process::id(), THREAD_INDEX).into_bytes();
    if !tag.is_empty() {
        head.extend_from_slice(crate::lexer::cstr(tag));
        head.extend_from_slice(b" | ");
    }
    head.truncate(MAX_LINE_LEN - 1);
    let msg = crate::lexer::cstr(msg);
    let room = MAX_LINE_LEN - head.len();
    let mut line = head;
    if msg.len() >= room {
        line.extend_from_slice(&msg[..room.saturating_sub(1)]);
        clean(&mut line);
        line.extend_from_slice(format!("... <Line truncated. Original size={}>\n", msg.len()).as_bytes());
    } else {
        line.extend_from_slice(msg);
        clean(&mut line);
        line.push(b'\n');
    }
    line
}

/// DisplayLog(level, tag, msg) before initialisation: to stderr.
pub fn display(state: &LogState, level: i32, tag: &str, msg: &[u8]) {
    if state.debug_level >= level {
        let line = format_line(state, tag.as_bytes(), msg);
        let _ = std::io::stderr().write_all(&line);
    }
}
