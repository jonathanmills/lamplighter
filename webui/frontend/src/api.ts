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
};
