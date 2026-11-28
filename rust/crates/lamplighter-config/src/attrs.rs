// SPDX-License-Identifier: CECILL-C
// Copyright (C) 2026 Jonathan Mills
// Translated from Robinhood's src/include/db_schema.def (as
// scripts/type_gen.pl generates db_schema.h) and src/include/list_mgr.h
// (Copyright (C) CEA/DAM, CeCILL-C).

//! Entry attribute indexes and attribute masks (`attr_mask_t`). The
//! attribute list is the same in every build (Lustre or not), so the
//! indexes are constants.

pub const ATTR_INDEX_PARENT_ID: u32 = 0;
pub const ATTR_INDEX_NAME: u32 = 1;
pub const ATTR_INDEX_PATH_UPDATE: u32 = 2;
pub const ATTR_INDEX_FULLPATH: u32 = 3;
pub const ATTR_INDEX_DEPTH: u32 = 4;
pub const ATTR_INDEX_DIRCOUNT: u32 = 5;
pub const ATTR_INDEX_AVGSIZE: u32 = 6;
pub const ATTR_INDEX_UID: u32 = 7;
pub const ATTR_INDEX_GID: u32 = 8;
pub const ATTR_INDEX_PROJID: u32 = 9;
pub const ATTR_INDEX_SIZE: u32 = 10;
pub const ATTR_INDEX_BLOCKS: u32 = 11;
pub const ATTR_INDEX_CREATION_TIME: u32 = 12;
pub const ATTR_INDEX_LAST_ACCESS: u32 = 13;
pub const ATTR_INDEX_LAST_MOD: u32 = 14;
pub const ATTR_INDEX_LAST_MDCHANGE: u32 = 15;
pub const ATTR_INDEX_TYPE: u32 = 16;
pub const ATTR_INDEX_MODE: u32 = 17;
pub const ATTR_INDEX_NLINK: u32 = 18;
pub const ATTR_INDEX_MD_UPDATE: u32 = 19;
pub const ATTR_INDEX_INVALID: u32 = 20;
pub const ATTR_INDEX_FILECLASS: u32 = 21;
pub const ATTR_INDEX_CLASS_UPDATE: u32 = 22;
pub const ATTR_INDEX_STRIPE_INFO: u32 = 23;
pub const ATTR_INDEX_STRIPE_ITEMS: u32 = 24;
pub const ATTR_INDEX_LINK: u32 = 25;
pub const ATTR_INDEX_RM_TIME: u32 = 26;

/// ATTR_MASK_<name>
pub const fn mask(index: u32) -> u32 {
    1 << index
}

pub const ATTR_INDEX_FLG_MASK: u32 = 0xFF00_0000;
pub const ATTR_INDEX_FLG_STATUS: u32 = 0x0100_0000;
pub const ATTR_INDEX_FLG_SMINFO: u32 = 0x0200_0000;
pub const ATTR_INDEX_FLG_COUNT: u32 = 0x0400_0000;
pub const ATTR_INDEX_FLG_UNSPEC: u32 = 0x0800_0000;

/// attr_mask_t: standard attributes, statuses, status manager info.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct AttrMask {
    pub std: u32,
    pub status: u32,
    pub sm_info: u64,
}

impl AttrMask {
    pub const NULL: AttrMask = AttrMask { std: 0, status: 0, sm_info: 0 };

    pub fn std(std: u32) -> AttrMask {
        AttrMask { std, ..AttrMask::NULL }
    }

    /// attr_mask_or
    pub fn or(&self, o: &AttrMask) -> AttrMask {
        AttrMask { std: self.std | o.std, status: self.status | o.status, sm_info: self.sm_info | o.sm_info }
    }

    /// attr_mask_set_index
    pub fn set_index(&mut self, index: u32) {
        if index & ATTR_INDEX_FLG_STATUS != 0 {
            self.status |= 1u32.wrapping_shl(index & !ATTR_INDEX_FLG_STATUS);
        } else if index & ATTR_INDEX_FLG_SMINFO != 0 {
            self.sm_info |= 1u64.wrapping_shl(index & !ATTR_INDEX_FLG_SMINFO);
        } else {
            self.std |= 1u32.wrapping_shl(index);
        }
    }

    /// The harness's dump: "std/status/sm_info" in hexadecimal.
    pub fn dump(&self) -> String {
        format!("{:x}/{:x}/{:x}", self.std, self.status, self.sm_info)
    }
}
