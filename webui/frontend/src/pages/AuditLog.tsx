// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Audit log (admin.audit.view): who signed in, looked up, exported and was
// refused, newest first. Filters live in the URL so a view can be shared.
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type AuditItem } from "../api";

const RANGES = [{ h: "24", label: "24 hours" }, { h: "168", label: "7 days" }, { h: "720", label: "30 days" }];
const OUTCOMES = ["ok", "denied", "failed", "error"];

// Routine "ok" stays quiet; only refusals and failures get a badge, so they stand out.
function Outcome({ value }: { value: string }) {
  if (value === "ok") return <span className="muted">ok</span>;
  return (
    <span className="status bad"><span className="badge-dot" aria-hidden="true">✕</span>{value}</span>
  );
}

export default function AuditLog() {
  const [params, setParams] = useSearchParams();
  const q = { user: params.get("user") ?? "", event: params.get("event") ?? "",
              outcome: params.get("outcome") ?? "", since_hours: params.get("hours") ?? "168" };
  const [items, setItems] = useState<AuditItem[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [userDraft, setUserDraft] = useState(q.user);

  const query = (before?: number) => {
    const p: Record<string, string> = { since_hours: q.since_hours, limit: "200" };
    if (q.user) p.user = q.user;
    if (q.event) p.event = q.event;
    if (q.outcome) p.outcome = q.outcome;
    if (before) p.before_id = String(before);
    return p;
  };

  useEffect(() => {
    setLoading(true);
    api.audit(query()).then((r) => { setItems(r.items); setEvents(r.events); setMore(r.more); })
      .catch((e) => setError(String(e.message ?? e))).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const loadMore = () => {
    const last = items[items.length - 1];
    if (!last) return;
    api.audit(query(last.id)).then((r) => { setItems([...items, ...r.items]); setMore(r.more); });
  };
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
  };

  if (error) return <section className="panel"><div className="empty"><h2>The audit log could not load</h2><p className="error-line">{error}</p></div></section>;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Audit log</h1>
          <p>Sign-ins, refusals, lookups and exports, newest first. The system journal keeps the authoritative copy.</p>
        </div>
      </div>

      <section className="panel" aria-label="Events">
        <div className="panel-head">
          <form className="toolbar" onSubmit={(e) => { e.preventDefault(); set("user", userDraft.trim()); }}>
            <div className="segmented" role="group" aria-label="Time range">
              {RANGES.map((r) => (
                <button key={r.h} type="button" aria-pressed={q.since_hours === r.h} onClick={() => set("hours", r.h)}>{r.label}</button>
              ))}
            </div>
            <input className="search" id="audit-user" style={{ flex: "0 1 220px" }} value={userDraft} onChange={(e) => setUserDraft(e.target.value)}
                   placeholder="Person (press Enter)" aria-label="Filter by person" />
            <select className="select" id="audit-event" value={q.event} onChange={(e) => set("event", e.target.value)} aria-label="Filter by event">
              <option value="">All events</option>
              {events.map((e) => <option key={e} value={e}>{e}</option>)}
            </select>
            <select className="select" id="audit-outcome" value={q.outcome} onChange={(e) => set("outcome", e.target.value)} aria-label="Filter by outcome">
              <option value="">All outcomes</option>
              {OUTCOMES.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </form>
          <span className="meta">{loading ? "Loading…" : `${items.length}${more ? "+" : ""} events`}</span>
        </div>
        <div className="panel-body">
          {!loading && !items.length ? <p className="muted">No events match these filters.</p> : (
            <div style={{ overflowX: "auto" }}>
              <table className="data-table">
                <thead><tr>
                  <th style={{ textAlign: "left" }}>Time</th><th style={{ textAlign: "left" }}>Person</th>
                  <th style={{ textAlign: "left" }}>Event</th><th style={{ textAlign: "left" }}>Outcome</th>
                  <th style={{ textAlign: "left" }}>Client</th><th style={{ textAlign: "left" }}>Details</th>
                </tr></thead>
                <tbody>{items.map((it) => (
                  <tr key={it.id}>
                    <td style={{ textAlign: "left" }} title={new Date(it.at * 1000).toISOString()}>{new Date(it.at * 1000).toLocaleString()}</td>
                    <td style={{ textAlign: "left" }}>{it.user
                      ? <button type="button" className="th-sort" onClick={() => { setUserDraft(it.user!); set("user", it.user!); }}><code>{it.user}</code></button>
                      : <span className="muted">—</span>}</td>
                    <td style={{ textAlign: "left" }}><code>{it.event}</code></td>
                    <td style={{ textAlign: "left" }}><Outcome value={it.outcome} /></td>
                    <td style={{ textAlign: "left" }} className="muted">{it.client ?? "—"}</td>
                    <td style={{ textAlign: "left", whiteSpace: "normal" }}>
                      <span className="chips">{Object.entries(it.details).map(([k, v]) => (
                        <span className="chip" key={k}>{k}: {Array.isArray(v) ? v.join(", ") : String(v)}</span>
                      ))}</span>
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          {more && <div style={{ marginTop: 12 }}><button type="button" className="btn btn-ghost" onClick={loadMore}>Load older events</button></div>}
          {q.user && <p className="muted" style={{ marginTop: 8 }}>Showing events for <code>{q.user}</code>. <Link to="/admin/audit" onClick={() => setUserDraft("")}>Show everyone</Link></p>}
        </div>
      </section>
    </>
  );
}
