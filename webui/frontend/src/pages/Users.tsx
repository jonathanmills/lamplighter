// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Users & groups: who uses the filesystem, from Robinhood's accounting
// (ACCT_STAT, live) with names from LDAP and change since the collector's
// last snapshot. Viewers see their own row; the API enforces it.
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type GroupsList, type UsersList } from "../api";
import { ago, bytes, int } from "../format";
import SortTable, { type Column } from "../components/SortTable";
import type { GroupRow, UserRow } from "../api";

export const signedBytes = (n: number | null) =>
  n === null ? "—" : n === 0 ? "no change" : `${n > 0 ? "+" : "−"}${bytes(Math.abs(n))}`;

export function sharePct(part: number, whole: number): string {
  const share = whole ? part / whole : 0;
  return share >= 0.01 ? `${Math.round(share * 100)}%` : share > 0 ? "<1%" : "0%";
}

export function ShareBar({ part, whole }: { part: number; whole: number }) {
  const share = whole ? part / whole : 0;
  const pct = sharePct(part, whole);
  return (
    <span className="bar-cell">{pct}<span className="share" aria-hidden="true">
      <span style={{ width: `${Math.max(share * 100, share > 0 ? 2 : 0)}%` }} /></span></span>
  );
}

const pctSmall = (small: number, files: number) => (files ? Math.round((small / files) * 100) : 0);

export default function Users() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "groups" ? "groups" : "users";
  const [q, setQ] = useState("");
  const [users, setUsers] = useState<UsersList | null>(null);
  const [groups, setGroups] = useState<GroupsList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.users(), api.groups()])
      .then(([u, g]) => { setUsers(u); setGroups(g); })
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  const needle = q.trim().toLowerCase();
  const userRows = useMemo(() => (users?.users ?? []).filter((u) => !needle ||
    [u.user, u.name ?? "", u.mail ?? ""].some((s) => s.toLowerCase().includes(needle))), [users, needle]);
  const groupRows = useMemo(() => (groups?.groups ?? []).filter((g) => !needle ||
    [g.group, g.description ?? ""].some((s) => s.toLowerCase().includes(needle))), [groups, needle]);

  if (error) return <section className="panel"><div className="empty"><h2>Usage could not load</h2><p className="error-line">{error}</p></div></section>;
  if (!users || !groups) return <div className="page-head"><div><h1>Users & groups</h1><p>Loading…</p></div></div>;

  const fs = users.filesystem_bytes;
  const top = [...users.users].sort((a, b) => b.bytes - a.bytes)[0];
  const mostSmall = [...users.users].sort((a, b) => b.small_files - a.small_files)[0];
  const since = users.snapshot_as_of ? `since the collector's snapshot ${ago(Math.floor(Date.now() / 1000) - users.snapshot_as_of)}` : "";

  const userCols: Column<UserRow>[] = [
    { key: "user", title: "User", align: "left", value: (r) => r.user,
      render: (r) => <Link to={`/usage/user/${encodeURIComponent(r.user)}`}><code>{r.user}</code></Link> },
    { key: "name", title: "Name", align: "left", value: (r) => r.name,
      render: (r) => r.name ?? <span className="muted">not in directory</span> },
    { key: "files", title: "Files", value: (r) => r.files, render: (r) => int(r.files) },
    { key: "dirs", title: "Directories", value: (r) => r.dirs, render: (r) => int(r.dirs) },
    { key: "bytes", title: "Volume", value: (r) => r.bytes, render: (r) => bytes(r.bytes) },
    { key: "share", title: "Share of filesystem", value: (r) => r.bytes, render: (r) => <ShareBar part={r.bytes} whole={fs} /> },
    { key: "small", title: "Small files (< 32 KiB)", value: (r) => pctSmall(r.small_files, r.files),
      render: (r) => `${pctSmall(r.small_files, r.files)}% · ${int(r.small_files)}` },
    { key: "change", title: "Change", value: (r) => r.change_bytes, render: (r) => signedBytes(r.change_bytes) },
  ];
  const groupCols: Column<GroupRow>[] = [
    { key: "group", title: "Group", align: "left", value: (r) => r.group, render: (r) => <code>{r.group}</code> },
    { key: "description", title: "Description", align: "left", value: (r) => r.description,
      render: (r) => r.description ?? <span className="muted">—</span> },
    { key: "members", title: "Members", value: (r) => r.members, render: (r) => r.members ?? "—" },
    { key: "users", title: "Owners with data", value: (r) => r.users, render: (r) => int(r.users) },
    { key: "files", title: "Files", value: (r) => r.files, render: (r) => int(r.files) },
    { key: "bytes", title: "Volume", value: (r) => r.bytes, render: (r) => bytes(r.bytes) },
    { key: "share", title: "Share of filesystem", value: (r) => r.bytes, render: (r) => <ShareBar part={r.bytes} whole={fs} /> },
    { key: "change", title: "Change", value: (r) => r.change_bytes, render: (r) => signedBytes(r.change_bytes) },
  ];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Users & groups</h1>
          <p>Who uses the filesystem, from Robinhood's accounting. Change is {since || "shown once the collector has run"}.</p>
        </div>
        <div className="toolbar">
          <div className="segmented" role="group" aria-label="Show">
            <button type="button" aria-pressed={view === "users"} onClick={() => setParams({})}>Users</button>
            <button type="button" aria-pressed={view === "groups"} onClick={() => setParams({ view: "groups" })}>Groups</button>
          </div>
          <input className="search" id="usage-search" type="search" placeholder={view === "users" ? "Find a user, name or e-mail" : "Find a group"}
                 value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter the table" />
        </div>
      </div>

      {users.scope === "self" && view === "users" && (
        <p className="note">You can see your own usage. Seeing every user needs the <code>usage.view_all_users</code> permission.</p>
      )}

      <section className="stats stats-4" aria-label="At a glance">
        <div className="stat"><span className="stat-label">Users with data</span><span className="stat-value">{int(users.users.length)}</span><span className="stat-sub">{users.scope === "self" ? "you" : "owners in Robinhood"}</span></div>
        <div className="stat"><span className="stat-label">Groups with data</span><span className="stat-value">{int(groups.groups.length)}</span><span className="stat-sub">Unix groups</span></div>
        {users.scope === "all" ? <>
          <div className="stat"><span className="stat-label">Largest user</span><span className="stat-value">{top ? bytes(top.bytes) : "—"}</span><span className="stat-sub">{top ? top.user : "—"}</span></div>
          <div className="stat"><span className="stat-label">Most small files</span><span className="stat-value">{mostSmall ? int(mostSmall.small_files) : "—"}</span><span className="stat-sub">{mostSmall ? `${mostSmall.user}, under 32 KiB` : "—"}</span></div>
        </> : <>
          <div className="stat"><span className="stat-label">Your volume</span><span className="stat-value">{top ? bytes(top.bytes) : "0 B"}</span><span className="stat-sub">{top ? `${int(top.files)} files` : "no data"}</span></div>
          <div className="stat"><span className="stat-label">Your share</span><span className="stat-value">{sharePct(top?.bytes ?? 0, fs)}</span><span className="stat-sub">of the filesystem</span></div>
        </>}
      </section>

      <section className="panel" aria-label={view === "users" ? "Users" : "Groups"}>
        <div className="panel-head"><h2>{view === "users" ? "Users" : "Groups"}</h2>
          <span className="meta">{view === "users" ? `${userRows.length} of ${users.users.length}` : `${groupRows.length} of ${groups.groups.length}`}</span></div>
        <div className="panel-body">
          {view === "users"
            ? <SortTable rows={userRows} columns={userCols} initialSort="bytes" rowKey={(r) => r.user} empty="No users match." />
            : <SortTable rows={groupRows} columns={groupCols} initialSort="bytes" rowKey={(r) => r.group} empty="No groups match." />}
        </div>
      </section>
    </>
  );
}
