// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills

//! User and group names, through the C library (getpwuid_r, getgrgid_r),
//! so NSS sources such as LDAP or SSSD apply exactly as for the C tools.

use std::ffi::CStr;

/// The name of user `uid`, or `None` if it has none.
pub fn user_name(uid: u32) -> Option<String> {
    let mut buf = vec![0u8; 4096];
    loop {
        let mut pwd: libc::passwd = unsafe { std::mem::zeroed() };
        let mut out: *mut libc::passwd = std::ptr::null_mut();
        // SAFETY: every pointer is valid for the call; buf outlives the result's strings.
        let rc = unsafe { libc::getpwuid_r(uid, &mut pwd, buf.as_mut_ptr().cast(), buf.len(), &mut out) };
        if rc == libc::ERANGE && buf.len() < 1 << 20 {
            buf.resize(buf.len() * 2, 0);
            continue;
        }
        if rc != 0 || out.is_null() {
            return None;
        }
        // SAFETY: on success pw_name points into buf, NUL-terminated.
        return Some(unsafe { CStr::from_ptr(pwd.pw_name) }.to_string_lossy().into_owned());
    }
}

/// The name of group `gid`, or `None` if it has none.
pub fn group_name(gid: u32) -> Option<String> {
    let mut buf = vec![0u8; 4096];
    loop {
        let mut grp: libc::group = unsafe { std::mem::zeroed() };
        let mut out: *mut libc::group = std::ptr::null_mut();
        // SAFETY: as in user_name.
        let rc = unsafe { libc::getgrgid_r(gid, &mut grp, buf.as_mut_ptr().cast(), buf.len(), &mut out) };
        if rc == libc::ERANGE && buf.len() < 1 << 20 {
            buf.resize(buf.len() * 2, 0);
            continue;
        }
        if rc != 0 || out.is_null() {
            return None;
        }
        // SAFETY: on success gr_name points into buf, NUL-terminated.
        return Some(unsafe { CStr::from_ptr(grp.gr_name) }.to_string_lossy().into_owned());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn root_is_root() {
        assert_eq!(user_name(0).as_deref(), Some("root"));
        assert_eq!(group_name(0).as_deref(), Some("root"));
    }

    #[test]
    fn unknown_ids_have_no_name() {
        assert_eq!(user_name(4_000_000_000), None);
        assert_eq!(group_name(4_000_000_000), None);
    }
}
