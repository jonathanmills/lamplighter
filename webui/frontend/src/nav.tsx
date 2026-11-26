// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Navigation model: one entry per page. The sidebar shows only the
// pages the user's permissions allow; the API enforces the same rules.
import type { ReactElement } from "react";

export interface NavItem {
  path: string;
  title: string;
  group: string;
  perm: string;
  icon: ReactElement;
  phase?: string;        // set while the page is not built yet
  blurb?: string;
}

const I = (d: string) => (
  <svg className="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

export const NAV: NavItem[] = [
  { path: "/", title: "Overview", group: "Monitor", perm: "dashboard.view",
    icon: I("M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z") },
  { path: "/usage", title: "Users & groups", group: "Monitor", perm: "usage.view_aggregates",
    icon: I("M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8") },
  { path: "/triage", title: "Triage", group: "Monitor", perm: "triage.view",
    icon: I("M22 12h-4l-3 9L9 3l-3 9H2"),
    phase: "Phase 4", blurb: "When the cluster feels slow: the busiest jobs from Lustre jobstats, matched to Slurm and to their owners' data." },
  { path: "/cold", title: "Cold data", group: "Data", perm: "cold.view",
    icon: I("M12 2v20M4.9 4.9l14.2 14.2M2 12h20M4.9 19.1 19.1 4.9") },
  { path: "/browse", title: "Browse", group: "Data", perm: "paths.view_own",
    icon: I("M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"),
    phase: "Phase 3", blurb: "Walk the directory tree with sizes, file counts and ages per subtree." },
  { path: "/admin/access", title: "Access control", group: "Administration", perm: "admin.acl",
    icon: I("M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"),
    phase: "Phase 2", blurb: "Roles and the LDAP groups that grant them." },
  { path: "/admin/directory", title: "Directory", group: "Administration", perm: "admin.ldap",
    icon: I("M4 4h16v16H4zM4 9h16M9 4v16") },
  { path: "/admin/audit", title: "Audit log", group: "Administration", perm: "admin.audit.view",
    icon: I("M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"),
    phase: "Phase 2", blurb: "Who signed in, searched and exported, and when." },
];

export const navByPath = (path: string) => NAV.find((n) => n.path === path);
