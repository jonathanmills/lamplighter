// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Directory (admin.ldap): what the UI uses to reach LDAP, a step-by-step
// connection test, "what does the UI see for this user?", and whether the
// configured role groups exist. Settings are read-only; they live in the
// config file, and secrets are never shown.
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, type DirConfig, type DirLookup, type DirMapping, type DirTest } from "../api";

function Status({ ok, yes = "Passed", no = "Failed" }: { ok: boolean; yes?: string; no?: string }) {
  return (
    <span className={"status " + (ok ? "ok" : "bad")}>
      <span className="badge-dot" aria-hidden="true">{ok ? "✓" : "✕"}</span>{ok ? yes : no}
    </span>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <div className="dl-row"><dt>{label}</dt><dd>{children}</dd></div>;
}

export default function Directory() {
  const [cfg, setCfg] = useState<DirConfig | null>(null);
  const [maps, setMaps] = useState<DirMapping[] | null>(null);
  const [test, setTest] = useState<DirTest | null>(null);
  const [testing, setTesting] = useState(false);
  const [who, setWho] = useState("");
  const [found, setFound] = useState<DirLookup | null>(null);
  const [lookupErr, setLookupErr] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.dirConfig().then(setCfg).catch((e) => setError(String(e.message ?? e)));
    api.dirMappings().then((m) => setMaps(m.mappings)).catch((e) => setError(String(e.message ?? e)));
  }, []);

  const runTest = () => {
    setTesting(true);
    api.dirTest().then(setTest).catch((e) => setError(String(e.message ?? e))).finally(() => setTesting(false));
  };
  const lookup = (e: FormEvent) => {
    e.preventDefault();
    if (!who.trim()) return;
    setFound(null); setLookupErr(null);
    api.dirLookup(who.trim()).then(setFound).catch((err) => setLookupErr(String(err.message ?? err)));
  };

  if (error) return <section className="panel"><div className="empty"><h2>Directory settings could not load</h2><p className="error-line">{error}</p></div></section>;
  if (!cfg) return <div className="page-head"><div><h1>Directory</h1><p>Loading…</p></div></div>;
  const l = cfg.ldap;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Directory</h1>
          <p>How the web UI reaches LDAP and decides who may do what. Settings come from the configuration file and are read-only here.</p>
        </div>
      </div>

      <section className="panel" aria-label="Configuration">
        <div className="panel-head"><h2>Configuration</h2><span className="meta">{l.directory_type}</span></div>
        <div className="panel-body">
          <dl className="dl-grid">
            <div className="dl-section">
              <div className="section-label">Connection</div>
              <Row label="Servers">{l.servers.map((s) => <div key={s}><code>{s}</code></div>)}</Row>
              <Row label="Encryption">{l.servers.every((s) => s.startsWith("ldaps://")) ? "LDAPS" : l.starttls ? "StartTLS" : "none (insecure)"}{l.allow_insecure ? " · certificate checks OFF" : " · certificate verified"}</Row>
              <Row label="CA file"><code>{l.ca_file ?? "system trust store"}</code></Row>
              <Row label="Service account"><code>{l.bind_dn}</code></Row>
              <Row label="Password file"><code>{l.bind_password_file}</code> <span className="muted">(contents never shown)</span></Row>
              <Row label="Timeout · cache">{l.timeout_seconds} s · {Math.round(l.cache_ttl_seconds / 60)} min</Row>
            </div>
            <div className="dl-section">
              <div className="section-label">Sign-in</div>
              <Row label="Single sign-on">{cfg.login.oidc ? <><code>{cfg.login.oidc.issuer}</code><div className="muted">client {cfg.login.oidc.client_id} · username from <code>{cfg.login.oidc.username_claim}</code></div></> : "off"}</Row>
              <Row label="Directory password">{cfg.login.ldap_bind ? "on" : "off"}</Row>
              <Row label="Session length">{cfg.login.session_hours} h</Row>
            </div>
            <div className="dl-section">
              <div className="section-label">Users</div>
              <Row label="Base DN"><code>{l.users.base_dn}</code> <span className="muted">({l.users.scope})</span></Row>
              <Row label="Filter"><code>{l.users.filter}</code></Row>
              <Row label="Attributes">login <code>{l.users.login_attr}</code>, name <code>{l.users.name_attr}</code>, e-mail <code>{l.users.mail_attr}</code>, <code>{l.users.uidnumber_attr}</code>, <code>{l.users.gidnumber_attr}</code></Row>
            </div>
            <div className="dl-section">
              <div className="section-label">Groups</div>
              <Row label="Base DN"><code>{l.groups.base_dn}</code> <span className="muted">({l.groups.scope})</span></Row>
              <Row label="Filter"><code>{l.groups.filter}</code></Row>
              <Row label="Membership">{l.groups.membership === "memberof" ? <>from the user's <code>memberOf</code></> : <>by searching group members</>}{l.groups.nested ? " · nested groups followed" : ""}</Row>
            </div>
          </dl>
        </div>
      </section>

      <section className="panel" aria-label="Connection test">
        <div className="panel-head"><h2>Connection test</h2>
          <button type="button" className="btn" onClick={runTest} disabled={testing}>{testing ? "Testing…" : test ? "Run again" : "Run test"}</button></div>
        <div className="panel-body">
          {!test && <p className="muted">Checks each server in turn: TLS and its certificate, the service account bind, and searches of the users and groups trees.</p>}
          {test && test.servers.map((srv) => (
            <div key={srv.server} style={{ display: "grid", gap: 6 }}>
              <div style={{ display: "flex", gap: 12, alignItems: "center" }}><code>{srv.server}</code><Status ok={srv.ok} /></div>
              <table className="data-table" style={{ whiteSpace: "normal" }}>
                <tbody>{srv.steps.map((st) => (
                  <tr key={st.step}>
                    <td style={{ width: 120 }}><Status ok={st.ok} yes="OK" /></td>
                    <td style={{ textAlign: "left", width: 190, whiteSpace: "nowrap" }}>{st.step}</td>
                    <td style={{ textAlign: "left" }}>{st.detail}</td>
                    <td style={{ width: 70 }} className="muted">{st.ms} ms</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ))}
        </div>
      </section>

      <section className="panel" aria-label="User lookup">
        <div className="panel-head"><h2>User lookup</h2><span className="meta">what the UI sees for one person</span></div>
        <div className="panel-body" style={{ gap: 12 }}>
          <form className="form-row" onSubmit={lookup}>
            <input className="search" id="lookup-user" value={who} onChange={(e) => setWho(e.target.value)}
                   placeholder={`Username (${l.users.login_attr})`} aria-label="Username to look up" />
            <button className="btn" type="submit">Look up</button>
          </form>
          {lookupErr && <p className="error-line">{lookupErr}</p>}
          {found && (
            <div style={{ display: "grid", gap: 12 }}>
              <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                <b>{found.name}</b><code>{found.dn}</code>
                <Status ok={found.can_sign_in} yes="Can sign in" no="Cannot sign in: no role" />
              </div>
              <dl className="dl-grid" style={{ margin: 0 }}>
                <div>
                  <Row label="E-mail">{found.mail ?? "—"}</Row>
                  <Row label="uidNumber · gidNumber">{found.uid_number ?? "—"} · {found.gid_number ?? "—"}</Row>
                </div>
                <div>
                  <Row label="Roles">{found.roles.length ? <span className="chips">{found.roles.map((r) => <span className="chip" key={r}>{r}</span>)}</span> : "none"}</Row>
                  <Row label="Permissions">{found.permissions.length} of 12</Row>
                </div>
              </dl>
              <table className="data-table">
                <thead><tr><th>Group</th><th style={{ textAlign: "left" }}>Grants</th><th style={{ textAlign: "left" }}>Because of</th></tr></thead>
                <tbody>{found.groups.map((g) => (
                  <tr key={g.group}><td><code>{g.group}</code></td>
                    <td style={{ textAlign: "left" }}>{g.roles.length ? <span className="chips">{g.roles.map((r) => <span className="chip" key={r}>{r}</span>)}</span> : <span className="muted">nothing</span>}</td>
                    <td style={{ textAlign: "left" }}>{g.via ?? <span className="muted">not mapped</span>}</td></tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <section className="panel" aria-label="Role mappings">
        <div className="panel-head"><h2>Role mappings</h2><span className="meta">LDAP groups that grant roles</span></div>
        <div className="panel-body">
          {!maps ? <p className="muted">Checking the groups…</p> : (
            <table className="data-table">
              <thead><tr><th>Group</th><th style={{ textAlign: "left" }}>Roles</th><th style={{ textAlign: "left" }}>Source</th><th>In the directory</th><th>Members</th></tr></thead>
              <tbody>{maps.map((m) => (
                <tr key={m.group + m.via}><td><code>{m.group}</code></td>
                  <td style={{ textAlign: "left" }}><span className="chips">{m.roles.map((r) => <span className="chip" key={r}>{r}</span>)}</span></td>
                  <td style={{ textAlign: "left" }}>{m.via}</td>
                  <td><Status ok={m.exists} yes="Found" no="Missing" /></td>
                  <td>{m.exists ? m.members : "—"}</td></tr>
              ))}</tbody>
            </table>
          )}
        </div>
      </section>
    </>
  );
}
