// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Fileclasses & policies: what Robinhood's fileclasses and policy statuses
// cover, from the newest collector run. Names are those of the site's
// Robinhood configuration. Counts only: thresholds belong to Checkmk.
import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type PolicyCounts } from "../api";
import { ago, bytes, int } from "../format";
import BarChart from "../components/BarChart";
import Panel from "../components/Panel";

const NONE = "(no fileclass)";
const statusLabel = (s: string) => (s === "" ? "not set" : s);

export default function Policies() {
  const [data, setData] = useState<PolicyCounts | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "none" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    api.policies().then((d) => { setData(d); setState("ok"); }).catch((e) => {
      if (e instanceof ApiError && e.status === 404) { setState("none"); return; }
      setError(String(e.message ?? e)); setState("error");
    });
  }, []);

  const classes = useMemo(() => Object.entries(data?.fileclasses ?? {})
    .map(([name, c]) => ({ name, label: name || NONE, ...c }))
    .sort((a, b) => (a.name === "" ? 1 : b.name === "" ? -1 : b.bytes - a.bytes || b.entries - a.entries)), [data]);
  const named = classes.filter((c) => c.name !== "");
  const policies = useMemo(() => Object.entries(data?.policies ?? {}).sort(([a], [b]) => a.localeCompare(b)), [data]);
  const fsBytes = data?.filesystem.bytes ?? 0;
  const now = Math.floor(Date.now() / 1000);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Fileclasses & policies</h1>
          <p>What the fileclasses and policies in Robinhood's configuration cover{data ? `, as of the collector run ${ago(now - data.as_of)}` : ""}.</p>
        </div>
      </div>

      {state === "none" && (
        <section className="panel"><div className="empty">
          <h2>No collector run yet</h2>
          <p className="muted">These counts come from the nightly collector. Run it once with <span className="cmd">lamplighter-collector run</span>.</p>
        </div></section>
      )}
      {state === "error" && <section className="panel"><div className="empty"><h2>Could not load</h2><p className="error-line">{error}</p></div></section>}

      {state === "ok" && data && (
        <div className="grid">
          {named.length ? (
            <Panel span={7} title="Volume by fileclass" meta="file data"
                   table={{ columns: ["Fileclass", "Volume", "Entries"], rows: named.map((c) => [c.name, bytes(c.bytes), int(c.entries)]) }}>
              <BarChart horizontal binary categories={named.map((c) => c.name)} values={named.map((c) => c.bytes)}
                        colorTokens="--chart-1" format={bytes}
                        ariaLabel={"Volume by fileclass: " + named.map((c) => `${c.name} ${bytes(c.bytes)}`).join(", ")} />
            </Panel>
          ) : (
            <section className="panel span-7" aria-label="No fileclasses">
              <div className="panel-head"><h2>No fileclasses yet</h2></div>
              <div className="panel-body"><p className="muted">Fileclasses are defined in Robinhood's configuration
                (<code>FileClass</code> blocks) and matched when entries are scanned.</p></div>
            </section>
          )}

          <section className="panel span-5" aria-label="Policy status">
            <div className="panel-head"><h2>Policy status</h2><span className="meta">entries per status</span></div>
            <div className="panel-body">
              {!policies.length ? <p className="muted">No policy with a status manager is configured.</p> : policies.map(([name, p]) => (
                <div key={name} className="dl-section">
                  <div className="section-label">{name} <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>· {p.field}</span></div>
                  <table className="data-table">
                    <thead><tr><th style={{ textAlign: "left" }}>Status</th><th>Entries</th><th>Volume</th></tr></thead>
                    <tbody>{Object.entries(p.statuses).sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b))).map(([s, v]) => (
                      <tr key={s}><td style={{ textAlign: "left" }}>{s === "" ? <span className="muted">not set</span> : <code>{s}</code>}</td>
                        <td>{int(v.entries)}</td><td>{bytes(v.bytes)}</td></tr>
                    ))}</tbody>
                  </table>
                </div>
              ))}
            </div>
          </section>

          <section className="panel span-12" aria-label="Fileclasses">
            <div className="panel-head"><h2>Fileclasses</h2>
              <span className="meta">an entry can match several fileclasses, so the rows can add up to more than the filesystem</span></div>
            <div className="panel-body" style={{ overflowX: "auto" }}>
              <table className="data-table">
                <thead><tr>
                  <th style={{ textAlign: "left" }}>Fileclass</th><th>Entries</th><th>Volume</th><th>Share of the volume</th>
                  {policies.map(([name]) => <th key={name} style={{ textAlign: "left" }}>{name}</th>)}
                </tr></thead>
                <tbody>{classes.map((c) => {
                  const share = fsBytes ? c.bytes / fsBytes : 0;
                  return (
                    <tr key={c.name}>
                      <td style={{ textAlign: "left" }}>{c.name ? <code>{c.name}</code> : <span className="muted">{NONE}</span>}</td>
                      <td>{int(c.entries)}</td>
                      <td>{bytes(c.bytes)}</td>
                      <td><span className="bar-cell">{share >= 0.001 ? `${(share * 100).toFixed(share < 0.1 ? 1 : 0)}%` : "<0.1%"}
                        <span className="share" aria-hidden="true"><span style={{ width: `${Math.min(100, share * 100)}%` }} /></span></span></td>
                      {policies.map(([name]) => {
                        const st = Object.entries(c.statuses[name] ?? {}).filter(([s]) => s !== "");
                        return <td key={name} style={{ textAlign: "left" }}>{st.length
                          ? <span className="chips">{st.map(([s, n]) => <span key={s} className="chip" title={`${int(n)} entries ${statusLabel(s)}`}>{s} {int(n)}</span>)}</span>
                          : <span className="muted">—</span>}</td>;
                      })}
                    </tr>
                  );
                })}</tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
