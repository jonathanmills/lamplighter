// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Typed client for the back end's /api/v1. Same origin, cookie session.

export interface Me {
  uid: string;
  name: string;
  mail: string | null;
  uid_number: number | null;
  gid_number: number | null;
  groups: string[];
  roles: string[];
  permissions: string[];
  login_method: "oidc" | "ldap";
}

export interface Status {
  filesystem: string | null;
  now: number;
  last_scan: {
    status?: string; started?: number; ended?: number; age_seconds?: number;
    entries?: number; errors?: number; timeouts?: number; interval_seconds?: number;
  };
  changelog?: { last_read?: number; last_cleared?: number; backlog_records?: number };
}

export interface UsageSummary {
  by_type: Record<string, { entries: number; bytes: number }>;
  top_users: { user: string; entries: number; bytes: number }[];
  file_sizes: { bucket: string; files: number }[];
}

export interface AgeBand { band: number; label: string; files: number; bytes: number }
export interface AgeBands { run_id: number; as_of: number; basis: "access" | "modify"; bands: AgeBand[] }

export interface ColdOwner {
  user: string; name: string | null; mail: string | null;
  cold_bytes: number; cold_files: number; total_bytes: number; oldest_band: number;
}
export interface ColdOwners { run_id: number; as_of: number; min_band: number; owners: ColdOwner[] }
export type ColdAge = "90d" | "1y" | "3y";

export interface OwnerUsage {
  files: number; dirs: number; entries: number; bytes: number; small_files: number;
  change_bytes: number | null;
}
export interface UserRow extends OwnerUsage { user: string; name: string | null; mail: string | null }
export interface GroupRow extends OwnerUsage { group: string; description: string | null; members: number | null; users: number }
export interface UsersList { scope: "all" | "self"; snapshot_as_of: number | null; filesystem_bytes: number; users: UserRow[] }
export interface GroupsList { snapshot_as_of: number | null; filesystem_bytes: number; groups: GroupRow[] }
export interface UserDetail {
  user: string; name: string | null; mail: string | null; uid_number: number | null;
  files: number; dirs: number; entries: number; bytes: number; small_files: number;
  filesystem_bytes: number;
  file_sizes: { bucket: string; files: number }[];
  groups: { group: string; entries: number; bytes: number }[];
  history: { at: number; bytes: number; entries: number }[];
  age: { as_of: number; bands: AgeBand[] } | null;
}

export interface DirConfig {
  ldap: {
    servers: string[]; starttls: boolean; ca_file: string | null; allow_insecure: boolean;
    bind_dn: string; bind_password_file: string; directory_type: string;
    timeout_seconds: number; cache_ttl_seconds: number;
    users: Record<string, string>; groups: { base_dn: string; scope: string; filter: string; membership: string; nested: boolean };
  };
  login: { oidc: { issuer: string; client_id: string; username_claim: string } | null; ldap_bind: boolean; session_hours: number };
}
export interface DirStep { step: string; ok: boolean; ms: number; detail: string }
export interface DirTest { ok: boolean; servers: { server: string; ok: boolean; steps: DirStep[] }[] }
export interface DirLookup {
  uid: string; dn: string; name: string; mail: string | null; uid_number: number | null; gid_number: number | null;
  groups: { group: string; roles: string[]; via: string | null }[];
  roles: string[]; permissions: string[]; can_sign_in: boolean;
}
export interface DirMapping { group: string; roles: string[]; via: string; exists: boolean; members: number }

export interface AuditItem { id: number; at: number; event: string; outcome: string; user: string | null; client: string | null; details: Record<string, unknown> }
export interface AuditPage { events: string[]; more: boolean; items: AuditItem[] }
export interface AccessInfo {
  permissions: { name: string; text: string }[];
  roles: { name: string; permissions: string[] }[];
  groups: { group: string; roles: string[]; via: string; members: string[] | null }[];
}

export interface DirTotals {
  id: string; name: string; path: string; depth: number; owner: string | null; top_owner: string | null;
  top_owner_bytes: number; subdirs: number; files: number; bytes: number;
  newest_access: number | null; oldest_access: number | null; cold_1y_bytes: number; cold_3y_bytes: number;
}
export interface LiveDir { id: string; name: string; owner: string | null; newest_access: number | null; live: true }
export interface BrowseResult {
  as_of: number; depth_limit: number;
  dir: { id: string; path: string; depth: number; totals: DirTotals | null };
  ancestors: { id: string; name: string; depth: number }[];
  subdirs: (DirTotals | LiveDir)[];
  files: { count: number; shown: number; items: { name: string; owner: string | null; size: number; last_access: number | null; last_mod: number | null }[] };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch("/api/v1" + path, { credentials: "same-origin", headers: { Accept: "application/json" }, ...init });
  if (r.status === 401) {
    window.location.assign("/auth/login");
    throw new ApiError(401, "Not signed in.");
  }
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(r.status, detail);
  }
  return r.json() as Promise<T>;
}

export const api = {
  me: () => get<Me>("/me"),
  status: () => get<Status>("/status"),
  usage: () => get<UsageSummary>("/usage/summary"),
  age: (basis: "access" | "modify" = "access") => get<AgeBands>(`/usage/age?basis=${basis}`),
  users: () => get<UsersList>("/usage/users"),
  groups: () => get<GroupsList>("/usage/groups"),
  user: (uid: string) => get<UserDetail>(`/usage/users/${encodeURIComponent(uid)}`),
  dirConfig: () => get<DirConfig>("/admin/directory/config"),
  dirTest: () => get<DirTest>("/admin/directory/test", { method: "POST" }),
  dirLookup: (user: string) => get<DirLookup>(`/admin/directory/lookup?user=${encodeURIComponent(user)}`),
  dirMappings: () => get<{ mappings: DirMapping[] }>("/admin/directory/mappings"),
  audit: (q: Record<string, string>) => get<AuditPage>("/admin/audit?" + new URLSearchParams(q).toString()),
  access: () => get<AccessInfo>("/admin/access"),
  browse: (dir?: string) => get<BrowseResult>("/browse" + (dir ? `?dir=${encodeURIComponent(dir)}` : "")),
  coldDirs: (olderThan: "1y" | "3y") => get<{ as_of: number; directories: DirTotals[] }>(`/cold/directories?older_than=${olderThan}`),
  coldOwners: (olderThan: ColdAge) => get<ColdOwners>(`/cold/owners?older_than=${olderThan}`),
  coldCsvUrl: (olderThan: ColdAge) => `/api/v1/cold/owners.csv?older_than=${olderThan}`,
};

/** The collector's six age bands, grouped into the four shown in charts
 *  (the validated four-step ordinal ramp). */
export const DISPLAY_BANDS: { label: string; bands: number[] }[] = [
  { label: "< 30 days", bands: [0, 1] },
  { label: "30 days – 1 year", bands: [2, 3] },
  { label: "1–3 years", bands: [4] },
  { label: "> 3 years", bands: [5] },
];

export function toDisplayBands(bands: AgeBand[]) {
  return DISPLAY_BANDS.map((d) => {
    const mine = bands.filter((b) => d.bands.includes(b.band));
    return { label: d.label, files: mine.reduce((a, b) => a + b.files, 0), bytes: mine.reduce((a, b) => a + b.bytes, 0) };
  });
}
