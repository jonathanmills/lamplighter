// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// Translated from Robinhood's src/common/rbh_misc.c and
// src/cfg_parsing/rbh_cfg_helpers.c (Copyright (C) CEA/DAM, CeCILL-C).

//! Value conversions, as the C code performs them. Where the C code relies
//! on the C library (sscanf's number parsing, strcasecmp), this calls the
//! same C library, so the results agree in every corner (signs, leading
//! spaces, overflow, locales).

use std::ffi::CString;
use std::os::raw::{c_char, c_double, c_int, c_ulonglong};

fn c(s: &[u8]) -> CString {
    CString::new(crate::lexer::cstr(s)).expect("no NUL after cstr")
}

/// A buffer for a sscanf "%s" conversion: the C code uses 256 bytes, which
/// a longer input would overflow; this one always fits.
fn sbuf(input: &[u8]) -> Vec<u8> {
    vec![0u8; input.len().max(256) + 2]
}

fn until_nul(b: &[u8]) -> Vec<u8> {
    crate::lexer::cstr(b).to_vec()
}

/// strcasecmp(a, b) == 0
pub fn eq_nocase(a: &[u8], b: &[u8]) -> bool {
    // SAFETY: two valid C strings.
    unsafe { libc::strcasecmp(c(a).as_ptr(), c(b).as_ptr()) == 0 }
}

/// sscanf(value, "%d%256s"): (items read, integer, rest)
pub fn scan_int(value: &[u8]) -> (c_int, i32, Vec<u8>) {
    let v = c(value);
    let mut n: c_int = 0;
    let mut rest = sbuf(value);
    // SAFETY: the format matches the arguments; rest is large enough.
    let nb = unsafe { libc::sscanf(v.as_ptr(), b"%d%256s\0".as_ptr() as *const c_char, &mut n, rest.as_mut_ptr()) };
    (nb, n, until_nul(&rest))
}

/// sscanf(value, "%lu%256s") (SCNu64 on 64-bit Linux)
pub fn scan_u64(value: &[u8]) -> (c_int, u64, Vec<u8>) {
    let v = c(value);
    let mut n: libc::c_ulong = 0;
    let mut rest = sbuf(value);
    // SAFETY: as above.
    let nb = unsafe { libc::sscanf(v.as_ptr(), b"%lu%256s\0".as_ptr() as *const c_char, &mut n, rest.as_mut_ptr()) };
    (nb, n as u64, until_nul(&rest))
}

/// sscanf(value, "%lf%255s")
pub fn scan_float(value: &[u8]) -> (c_int, f64, Vec<u8>) {
    let v = c(value);
    let mut n: c_double = 0.0;
    let mut rest = sbuf(value);
    // SAFETY: as above.
    let nb = unsafe { libc::sscanf(v.as_ptr(), b"%lf%255s\0".as_ptr() as *const c_char, &mut n, rest.as_mut_ptr()) };
    (nb, n, until_nul(&rest))
}

/// str2bool: 1, 0, or -1 when not a boolean.
pub fn str2bool(s: &[u8]) -> i32 {
    let s = crate::lexer::cstr(s);
    if s == b"1" || ["true", "yes", "enabled", "on"].iter().any(|w| eq_nocase(s, w.as_bytes())) {
        return 1;
    }
    if s == b"0" || ["false", "no", "disabled", "off"].iter().any(|w| eq_nocase(s, w.as_bytes())) {
        return 0;
    }
    -1
}

/// str2duration: seconds, or -1 (also the value "-1s"). Computed in a C
/// int, so large values wrap as they do in C.
pub fn str2duration(s: &[u8]) -> i32 {
    let v = c(s);
    let mut d: c_int = 0;
    let mut suffix = sbuf(s);
    // SAFETY: as above.
    let nb = unsafe { libc::sscanf(v.as_ptr(), b"%d%s\0".as_ptr() as *const c_char, &mut d, suffix.as_mut_ptr()) };
    if nb <= 0 {
        return -1;
    }
    let suffix = until_nul(&suffix);
    if nb == 1 || suffix.is_empty() {
        return d;
    }
    let is = |a: &str, b: &str| eq_nocase(&suffix, a.as_bytes()) || eq_nocase(&suffix, b.as_bytes());
    const MINUTE: i32 = 60;
    const HOUR: i32 = 60 * MINUTE;
    const DAY: i32 = 24 * HOUR;
    if is("s", "sec") {
        d
    } else if is("m", "min") {
        MINUTE.wrapping_mul(d)
    } else if is("h", "hour") {
        HOUR.wrapping_mul(d)
    } else if is("d", "day") {
        DAY.wrapping_mul(d)
    } else if is("w", "week") {
        (7 * DAY).wrapping_mul(d)
    } else if is("y", "year") {
        (365 * DAY).wrapping_mul(d)
    } else {
        -1
    }
}

/// str2size: bytes, or u64::MAX when invalid. Unsigned arithmetic wraps
/// as in C.
pub fn str2size(s: &[u8]) -> u64 {
    let v = c(s);
    let mut size: c_ulonglong = 0;
    let mut suffix = sbuf(s);
    // SAFETY: as above.
    let nb = unsafe { libc::sscanf(v.as_ptr(), b"%llu%s\0".as_ptr() as *const c_char, &mut size, suffix.as_mut_ptr()) };
    if nb <= 0 {
        return u64::MAX;
    }
    let suffix = until_nul(&suffix);
    let size = size as u64;
    if nb == 1 || suffix.is_empty() {
        return size;
    }
    let is = |a: &str, b: &str| eq_nocase(&suffix, a.as_bytes()) || eq_nocase(&suffix, b.as_bytes());
    const K: u64 = 1024;
    if eq_nocase(&suffix, b"B") {
        size
    } else if is("kB", "K") {
        K.wrapping_mul(size)
    } else if is("MB", "M") {
        (K * K).wrapping_mul(size)
    } else if is("GB", "G") {
        (K * K * K).wrapping_mul(size)
    } else if is("TB", "T") {
        (K * K * K * K).wrapping_mul(size)
    } else if is("PB", "P") {
        (K * K * K * K * K).wrapping_mul(size)
    } else if is("EB", "E") {
        (K * K * K * K * K * K).wrapping_mul(size)
    } else {
        u64::MAX
    }
}

/// str2int: an int, or -1 when invalid (sscanf "%d%s": a suffix makes it
/// invalid).
pub fn str2int(s: &[u8]) -> i32 {
    let v = c(s);
    let mut d: c_int = 0;
    let mut suffix = sbuf(s);
    // SAFETY: as above.
    let nb = unsafe { libc::sscanf(v.as_ptr(), b"%d%s\0".as_ptr() as *const c_char, &mut d, suffix.as_mut_ptr()) };
    if nb <= 0 {
        return -1;
    }
    if nb == 1 || suffix[0] == 0 {
        d
    } else {
        -1
    }
}

#[cfg(target_os = "linux")]
fn errno_ptr() -> *mut c_int {
    // SAFETY: always valid for the calling thread.
    unsafe { libc::__errno_location() }
}
#[cfg(not(target_os = "linux"))]
fn errno_ptr() -> *mut c_int {
    // SAFETY: always valid for the calling thread.
    unsafe { libc::__error() }
}

/// strtol(s, &end, 0): (value, errno, whether any digit was read)
pub fn strtol0(s: &[u8]) -> (i64, c_int, bool) {
    let v = c(s);
    let mut end: *mut c_char = std::ptr::null_mut();
    // SAFETY: a valid C string; errno is this thread's.
    unsafe {
        *errno_ptr() = 0;
        let n = libc::strtol(v.as_ptr(), &mut end, 0);
        let e = *errno_ptr();
        (n as i64, e, end as *const c_char != v.as_ptr())
    }
}

/// snprintf with one double argument, via the C library.
fn fmt_f64(format: &[u8], v: f64) -> Vec<u8> {
    let mut buf = vec![0u8; 512];
    // SAFETY: format is NUL-terminated and takes one double.
    let n = unsafe { libc::snprintf(buf.as_mut_ptr() as *mut c_char, buf.len(), format.as_ptr() as *const c_char, v) };
    buf.truncate((n.max(0) as usize).min(buf.len() - 1));
    buf
}

/// FormatFileSize
pub fn format_file_size(size: u64) -> Vec<u8> {
    const K: u64 = 1024;
    let units: [(u64, &[u8]); 6] = [(K, b"%.2f KB\0"), (K * K, b"%.2f MB\0"), (K * K * K, b"%.2f GB\0"),
        (K * K * K * K, b"%.2f TB\0"), (K * K * K * K * K, b"%.2f PB\0"), (K * K * K * K * K * K, b"%.2f EB\0")];
    if size < K {
        return size.to_string().into_bytes();
    }
    for (i, (unit, format)) in units.iter().enumerate() {
        if i + 1 == units.len() || size < unit.wrapping_mul(K) {
            return fmt_f64(format, (0.0 + size as f64) / (0.0 + *unit as f64));
        }
    }
    unreachable!()
}

/// FormatDurationFloat (computed in float, as the C code does)
pub fn format_duration_float(duration: i64) -> Vec<u8> {
    let d = if duration < 0 { duration.wrapping_neg() } else { duration };
    let days = d as f32 / 86400.0;
    let hours = d as f32 / 3600.0;
    let minutes = d as f32 / 60.0;
    if days >= 1.0 {
        fmt_f64(b"%.1fd\0", days as f64)
    } else if hours >= 1.0 {
        fmt_f64(b"%.1fh\0", hours as f64)
    } else if minutes >= 1.0 {
        fmt_f64(b"%.1fmin\0", minutes as f64)
    } else {
        format!("{:02}s", d as u32).into_bytes()
    }
}

/// printf("%.2f") of a double, via the C library.
pub fn fmt_2f(v: f64) -> Vec<u8> {
    let mut buf = vec![0u8; 512];
    // SAFETY: snprintf writes at most buf.len() bytes, NUL included.
    let n = unsafe { libc::snprintf(buf.as_mut_ptr() as *mut c_char, buf.len(), b"%.2f\0".as_ptr() as *const c_char, v) };
    buf.truncate((n.max(0) as usize).min(buf.len() - 1));
    buf
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn durations_and_sizes() {
        assert_eq!(str2duration(b"10"), 10);
        assert_eq!(str2duration(b"2min"), 120);
        assert_eq!(str2duration(b" 1d"), 86400);
        assert_eq!(str2duration(b"1x"), -1);
        assert_eq!(str2duration(b"abc"), -1);
        assert_eq!(str2size(b"10GB"), 10 << 30);
        assert_eq!(str2size(b"3k"), 3072);
        assert_eq!(str2size(b"1Q"), u64::MAX);
        assert_eq!(str2bool(b"Yes"), 1);
        assert_eq!(str2bool(b"OFF"), 0);
        assert_eq!(str2bool(b"maybe"), -1);
    }
}
