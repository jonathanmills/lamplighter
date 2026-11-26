// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Access control (admin.acl), read-only: what each role may do, and who
// holds each role through which LDAP group. Changes are made in the
// configuration file (access.role_mappings), which stays the source of truth.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AccessInfo } from "../api";

export default function Access() {
  const [info, setInfo] = useState<AccessInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.access().then(setInfo).catch((e) => setError(String(e.message ?? e))); }, []);

  if (error) return <section className="panel"><div className="empty"><h2>Access control could not load</h2><p className="error-line">{error}</p></div></section>;
  if (!info) return <div className="page-head"><div><h1>Access control</h1><p>Loading…</p></div></div>;

  const people = new Set(info.groups.flatMap((g) => g.members ?? []));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Access control</h1>
          <p>Permissions are grouped into roles; LDAP groups grant roles. This page is read-only: roles are assigned in the configuration file (<code>access.role_mappings</code>).</p>
        </div>
      </div>

      <section className="panel" aria-label="Who has access">
        <div className="panel-head"><h2>Who has access</h2><span className="meta">{people.size} {people.size === 1 ? "person" : "people"} through {info.groups.length} groups</span></div>
        <div className="panel-body" style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead><tr><th style={{ textAlign: "left" }}>LDAP group</th><th style={{ textAlign: "left" }}>Grants</th>
              <th style={{ textAlign: "left" }}>Source</th><th style={{ textAlign: "left" }}>Members</th></tr></thead>
            <tbody>{info.groups.map((g) => (
              <tr key={g.group + g.via}>
                <td style={{ textAlign: "left" }}><code>{g.group}</code></td>
                <td style={{ textAlign: "left" }}><span className="chips">{g.roles.map((r) => <span className="chip" key={r}>{r}</span>)}</span></td>
                <td style={{ textAlign: "left" }}>{g.via}</td>
                <td style={{ textAlign: "left", whiteSpace: "normal" }}>
                  {g.members === null ? <span className="status bad"><span className="badge-dot" aria-hidden="true">✕</span>Group not found in the directory</span>
                    : g.members.length === 0 ? <span className="muted">no members</span>
                    : <span className="chips">{g.members.map((m) => <Link key={m} to={`/usage/user/${encodeURIComponent(m)}`} className="chip">{m}</Link>)}</span>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>

      <section className="panel" aria-label="Roles and permissions">
        <div className="panel-head"><h2>Roles and permissions</h2><span className="meta">✓ = the role includes the permission</span></div>
        <div className="panel-body" style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead><tr>
              <th style={{ textAlign: "left" }}>Permission</th>
              {info.roles.map((r) => <th key={r.name} style={{ textAlign: "center" }}>{r.name}</th>)}
            </tr></thead>
            <tbody>{info.permissions.map((p) => (
              <tr key={p.name}>
                <td style={{ textAlign: "left" }}>{p.text} <span className="muted"><code>{p.name}</code></span></td>
                {info.roles.map((r) => (
                  <td key={r.name} style={{ textAlign: "center" }}>
                    {r.permissions.includes(p.name)
                      ? <span className="status ok" aria-label="included"><span className="badge-dot" aria-hidden="true">✓</span></span>
                      : <span className="muted" aria-label="not included">—</span>}
                  </td>
                ))}
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>
    </>
  );
}
