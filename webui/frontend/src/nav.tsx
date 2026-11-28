// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Navigation model: one entry per page. The sidebar shows only the
// pages the user's permissions allow; the API enforces the same rules.
import type { ReactElement } from "react";

export interface NavItem {
  path: string;
  title: string;
  group: string;
  perm: string | string[];   // any of these
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
    icon: I("M22 12h-4l-3 9L9 3l-3 9H2") },
  { path: "/cold", title: "Cold data", group: "Data", perm: "cold.view",
    icon: I("M12 2v20M4.9 4.9l14.2 14.2M2 12h20M4.9 19.1 19.1 4.9") },
  { path: "/policies", title: "Fileclasses & policies", group: "Data", perm: "usage.view_aggregates",
    icon: I("M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8zM7.5 7.5h.01") },
  { path: "/search", title: "Search", group: "Data", perm: "paths.view_all",
    icon: I("M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35") },
  { path: "/browse", title: "Browse", group: "Data", perm: ["paths.view_all", "paths.view_own"],
    icon: I("M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z") },
  { path: "/reports", title: "Reports", group: "Data",
    perm: ["reports.manage", "usage.view_aggregates", "cold.view", "triage.view", "paths.view_all"],
    icon: I("M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 17v-3M12 17v-6M16 17v-4") },
  { path: "/admin/access", title: "Access control", group: "Administration", perm: "admin.acl",
    icon: I("M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z") },
  { path: "/admin/directory", title: "Directory", group: "Administration", perm: "admin.ldap",
    icon: I("M4 4h16v16H4zM4 9h16M9 4v16") },
  { path: "/admin/audit", title: "Audit log", group: "Administration", perm: "admin.audit.view",
    icon: I("M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11") },
];

export const navByPath = (path: string) => NAV.find((n) => n.path === path);
