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

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch("/api/v1" + path, { credentials: "same-origin", headers: { Accept: "application/json" } });
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
