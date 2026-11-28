// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Scheduled reports (reports.py): one of the UI's tables with saved
// parameters, run on a schedule as its owner, with a history that a
// custom graph can plot, stacked by a column. The equivalent of the old
// PHP GUI's "tasks", custom graphs and stacked graphs.
//   /reports           the reports this person may see
//   /reports/new       the editor (reports.manage)
//   /reports/:id       results, graph and run history
//   /reports/:id/edit  the editor
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type ReportDef, type ReportDetail, type ReportRow, type ReportSources, type Schedule } from "../api";
import { ago, bytes, int } from "../format";
import StackedChart from "../components/StackedChart";
import { exportUrl } from "../components/Download";

const now = () => Math.floor(Date.now() / 1000);
const SCHEDULES: { key: Schedule; label: string }[] = [
  { key: "manual", label: "Only when run by hand" }, { key: "hourly", label: "Every hour" },
  { key: "daily", label: "Every day" }, { key: "weekly", label: "Every week" }];
const scheduleText = (s: Schedule) => SCHEDULES.find((x) => x.key === s)!.label.replace(/^Every /, "every ");
const isBytes = (col: string) => /bytes|volume|size/.test(col);
const cellText = (col: string, v: unknown) =>
  v === null || v === undefined ? "—"
    : typeof v === "number" ? (isBytes(col) ? bytes(v) : int(v))
      : typeof v === "object" ? JSON.stringify(v) : String(v);

function RunStatus({ r }: { r: ReportDetail["runs"][number] | null }) {
  if (!r) return <span className="muted">never run</span>;
  const text = r.status === "ok" ? `${int(r.rows)} rows` : r.status === "denied" ? "not allowed to run" : "failed";
  return <span className={r.status === "ok" ? "" : "error-line"} title={r.message ?? undefined}>{text}, {ago(now() - r.started_at)}</span>;
}

export function ReportsList() {
  const [data, setData] = useState<{ may_manage: boolean; reports: ReportRow[] } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.reports().then(setData).catch((e) => setErr(String(e.message ?? e))); }, []);
  if (err) return <section className="panel"><div className="empty"><h2>Reports could not load</h2><p className="muted">{err}</p></div></section>;
  if (!data) return <div className="page-head"><div><h1>Reports</h1><p>Loading…</p></div></div>;
  return (
    <>
      <div className="page-head">
        <div><h1>Reports</h1>
          <p>Tables from these pages, saved with their settings and run on a schedule. Each run is kept for history and graphs.</p></div>
        {data.may_manage && <div className="actions"><Link className="btn" to="/reports/new">New report</Link></div>}
      </div>
      <section className="panel" aria-label="Reports">
        <div className="panel-head"><h2>Reports</h2><span className="meta">{data.reports.length} you may see</span></div>
        <div className="panel-body" style={{ overflowX: "auto" }}>
          {!data.reports.length ? <p className="muted">{data.may_manage ? "No reports yet: create one with New report." : "No reports you may see yet."}</p> : (
            <table className="data-table">
              <thead><tr><th style={{ textAlign: "left" }}>Report</th><th style={{ textAlign: "left" }}>Table</th>
                <th style={{ textAlign: "left" }}>Runs</th><th style={{ textAlign: "left" }}>Last run</th><th style={{ textAlign: "left" }}>Owner</th></tr></thead>
              <tbody>{data.reports.map((r) => (
                <tr key={r.id}>
                  <td style={{ textAlign: "left" }}><Link to={`/reports/${r.id}`}>{r.name}</Link></td>
                  <td style={{ textAlign: "left" }}>{r.source_title}</td>
                  <td style={{ textAlign: "left" }}>{scheduleText(r.schedule)}{r.keep_runs > 1 ? ` · keeps ${r.keep_runs}` : ""}</td>
                  <td style={{ textAlign: "left" }}><RunStatus r={r.last_run} /></td>
                  <td style={{ textAlign: "left" }}><code>{r.owner}</code></td>
                </tr>))}</tbody>
            </table>)}
        </div>
      </section>
    </>
  );
}

export function ReportView() {
  const id = Number(useParams().id);
  const nav = useNavigate();
  const [r, setR] = useState<ReportDetail | null>(null);
  const [mayManage, setMayManage] = useState(false);
  const [err, setErr] = useState<{ status: number; msg: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.report(id).then((x) => { setR(x); document.title = `${x.name} · Reports · Lamplighter`; })
    .catch((e) => setErr({ status: e instanceof ApiError ? e.status : 0, msg: String(e.message ?? e) }));
  useEffect(() => { load(); api.reportSources().then((s) => setMayManage(s.may_manage)).catch(() => {}); }, [id]);
  const fmt = useMemo(() => (r?.series && isBytes(r.series.value) ? bytes : int), [r]);

  if (err) return <section className="panel"><div className="empty"><h2>{err.status === 404 ? "No such report" : "This report could not load"}</h2>
    <p className="muted">{err.msg}</p><Link to="/reports">All reports</Link></div></section>;
  if (!r) return <div className="page-head"><div><h1>Report</h1><p>Loading…</p></div></div>;

  const runNow = () => { setBusy(true); api.runReport(id).then(load).catch((e) => alert(e.message)).finally(() => setBusy(false)); };
  const remove = () => { if (confirm(`Delete the report “${r.name}” and its ${r.runs.length} kept runs?`)) api.deleteReport(id).then(() => nav("/reports")).catch((e) => alert(e.message)); };
  const t = r.latest;
  return (
    <>
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <h1>{r.name}</h1>
          <p>{r.source_title}, {scheduleText(r.schedule)}, keeps {r.keep_runs === 1 ? "the latest run" : `${r.keep_runs} runs`}.
            Runs as <code>{r.owner}</code>{r.recipients.length ? <>, emailed to {r.recipients.map((u) => <code key={u}> {u}</code>)}</> : null}.</p>
        </div>
        {mayManage && <div className="actions">
          <button className="btn" type="button" onClick={runNow} disabled={busy}>{busy ? "Running…" : "Run now"}</button>
          <Link className="btn btn-ghost" to={`/reports/${id}/edit`}>Edit</Link>
          <button className="btn btn-ghost" type="button" onClick={remove}>Delete</button>
        </div>}
      </div>

      {r.series && r.series.times.length > 0 && (
        <section className="panel" aria-label="Graph">
          <div className="panel-head"><h2>{r.series.value}{r.series.stack_by ? ` by ${r.series.stack_by}` : ""}</h2>
            <span className="meta">over the {r.series.times.length} kept runs</span></div>
          <div className="panel-body">
            <StackedChart times={r.series.times} series={r.series.series} format={fmt} stacked={!!r.series.stack_by}
              ariaLabel={`${r.series.value} over the kept runs${r.series.stack_by ? `, stacked by ${r.series.stack_by}` : ""}`} />
          </div>
        </section>
      )}

      <section className="panel" aria-label="Latest results">
        <div className="panel-head"><h2>Latest results</h2>
          {t && <><span className="meta">{int(t.rows.length)} rows · {ago(now() - t.started_at)}</span>
            <span className="download">Download <a href={exportUrl(`/reports/${id}/runs/${t.run_id}`, {}, "csv")} download>CSV</a> · <a href={exportUrl(`/reports/${id}/runs/${t.run_id}`, {}, "json")} download>JSON</a></span></>}
        </div>
        <div className="panel-body" style={{ overflowX: "auto" }}>
          {!t ? <p className="muted">No successful run yet{mayManage ? ": use Run now." : "."}</p> : (
            <table className="data-table">
              <thead><tr>{t.columns.map((c) => <th key={c} style={{ textAlign: typeof t.rows[0]?.[c] === "number" ? "right" : "left" }}>{c}</th>)}</tr></thead>
              <tbody>{t.rows.slice(0, 500).map((row, i) => (
                <tr key={i}>{t.columns.map((c) => <td key={c} style={{ textAlign: typeof row[c] === "number" ? "right" : "left" }}>{cellText(c, row[c])}</td>)}</tr>))}</tbody>
            </table>)}
          {t && t.rows.length > 500 && <p className="muted">Showing 500 of {int(t.rows.length)} rows: download for all.</p>}
        </div>
      </section>

      <section className="panel" aria-label="Runs">
        <div className="panel-head"><h2>Runs</h2><span className="meta">newest first</span></div>
        <div className="panel-body" style={{ overflowX: "auto" }}>
          {!r.runs.length ? <p className="muted">Not run yet.</p> : (
            <table className="data-table">
              <thead><tr><th style={{ textAlign: "left" }}>Started</th><th style={{ textAlign: "left" }}>By</th><th style={{ textAlign: "left" }}>Outcome</th>
                <th>Rows</th><th>Seconds</th><th style={{ textAlign: "left" }}>Note</th><th style={{ textAlign: "left" }}>Download</th></tr></thead>
              <tbody>{r.runs.map((x) => (
                <tr key={x.id}>
                  <td style={{ textAlign: "left" }}>{new Date(x.started_at * 1000).toLocaleString()}</td>
                  <td style={{ textAlign: "left" }}>{x.trigger === "schedule" ? "schedule" : <code>{x.trigger}</code>}</td>
                  <td style={{ textAlign: "left" }} className={x.status === "ok" ? "" : "error-line"}>{x.status === "ok" ? "done" : x.status === "denied" ? "not allowed" : "failed"}</td>
                  <td>{int(x.rows)}</td><td>{x.seconds ?? "—"}</td>
                  <td style={{ textAlign: "left", maxWidth: 420, overflowWrap: "anywhere" }}>{x.message ?? ""}</td>
                  <td style={{ textAlign: "left" }}>{x.status === "ok" && <><a href={exportUrl(`/reports/${id}/runs/${x.id}`, {}, "csv")} download>CSV</a> · <a href={exportUrl(`/reports/${id}/runs/${x.id}`, {}, "json")} download>JSON</a></>}</td>
                </tr>))}</tbody>
            </table>)}
        </div>
      </section>
    </>
  );
}

const EMPTY: ReportDef = { name: "", source: "", params: {}, schedule: "daily", keep_runs: 1, recipients: [], chart: null };

export function ReportEditor() {
  const idParam = useParams().id;
  const id = idParam ? Number(idParam) : null;
  const nav = useNavigate();
  const [src, setSrc] = useState<ReportSources | null>(null);
  const [d, setD] = useState<ReportDef>(EMPTY);
  const [recipients, setRecipients] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.reportSources().then((s) => {
      setSrc(s);
      if (id === null) setD((x) => ({ ...x, source: x.source || s.sources[0]?.key || "" }));
    }).catch((e) => setErr(String(e.message ?? e)));
    if (id !== null) api.report(id).then((r) => {
      setD({ name: r.name, source: r.source, params: r.params, schedule: r.schedule, keep_runs: r.keep_runs,
        recipients: r.recipients, chart: r.chart });
      setRecipients(r.recipients.join(", "));
    }).catch((e) => setErr(String(e.message ?? e)));
  }, [id]);

  if (!src) return <div className="page-head"><div><h1>Report</h1><p>{err || "Loading…"}</p></div></div>;
  if (!src.may_manage) return <section className="panel"><div className="empty"><h2>You may not manage reports</h2>
    <p className="muted">Creating and editing reports needs the reports.manage permission.</p></div></section>;
  const source = src.sources.find((s) => s.key === d.source);
  const setParam = (k: string, v: string) => setD({ ...d, params: { ...d.params, [k]: v === "" ? null : v } });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    const body = { ...d, recipients: recipients.split(/[\s,]+/).filter(Boolean) };
    (id === null ? api.createReport(body) : api.updateReport(id, body))
      .then((x) => nav(`/reports/${x.id}`)).catch((e2) => setErr(String(e2.message ?? e2))).finally(() => setBusy(false));
  };

  return (
    <>
      <div className="page-head"><div><h1>{id === null ? "New report" : `Edit ${d.name}`}</h1>
        <p>The report runs with your permissions{id !== null ? " (saving makes you its owner)" : ""}. Its results are visible to people who may see the same table.</p></div></div>
      <section className="panel" aria-label="Report">
        <div className="panel-body">
          <form onSubmit={submit}>
            <div className="form-grid">
              <label className="field">Name<input className="search" required maxLength={100} value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="e.g. Weekly cold owners" /></label>
              <label className="field">Table
                <select className="select" value={d.source} onChange={(e) => setD({ ...d, source: e.target.value, params: {}, chart: null })}>
                  {src.sources.map((s) => <option key={s.key} value={s.key}>{s.title}</option>)}
                </select></label>
              {source?.params.map((p) => (
                <label className="field" key={p.name}>{p.label}
                  {p.kind === "choice"
                    ? <select className="select" value={String(d.params[p.name] ?? p.default ?? "")} onChange={(e) => setParam(p.name, e.target.value)}>
                        {p.choices.map((c) => <option key={c} value={c}>{c}</option>)}</select>
                    : <input className="search" type={p.kind === "int" ? "number" : "text"} min={p.min ?? undefined} max={p.max ?? undefined}
                        value={String(d.params[p.name] ?? "")} placeholder={p.default !== null ? String(p.default) : ""}
                        onChange={(e) => setParam(p.name, e.target.value)} />}
                </label>))}
              <label className="field">Schedule
                <select className="select" value={d.schedule} onChange={(e) => setD({ ...d, schedule: e.target.value as Schedule })}>
                  {SCHEDULES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select></label>
              <label className="field">Runs to keep (1 = latest only)
                <input className="search" type="number" min={1} max={src.max_keep_runs} value={d.keep_runs}
                  onChange={(e) => setD({ ...d, keep_runs: Number(e.target.value) })} /></label>
              <label className="field">Email to (directory user names)
                <input className="search" value={recipients} onChange={(e) => setRecipients(e.target.value)} disabled={!src.email}
                  placeholder={src.email ? "e.g. carol, dave" : "email is not configured on this server"} /></label>
            </div>
            <h2 style={{ fontSize: 14, margin: "18px 0 8px" }}>Graph over the kept runs (optional)</h2>
            <div className="form-grid">
              <label className="field">Value column
                <input className="search" value={d.chart?.value ?? ""} placeholder="e.g. bytes"
                  onChange={(e) => setD({ ...d, chart: e.target.value ? { value: e.target.value, stack_by: d.chart?.stack_by ?? null, top: d.chart?.top ?? 8 } : null })} /></label>
              <label className="field">Stacked by column
                <input className="search" value={d.chart?.stack_by ?? ""} placeholder="e.g. user (empty: one total line)" disabled={!d.chart}
                  onChange={(e) => d.chart && setD({ ...d, chart: { ...d.chart, stack_by: e.target.value || null } })} /></label>
              <label className="field">Largest series shown (the rest: Other)
                <input className="search" type="number" min={1} max={8} value={d.chart?.top ?? 8} disabled={!d.chart}
                  onChange={(e) => d.chart && setD({ ...d, chart: { ...d.chart, top: Number(e.target.value) } })} /></label>
            </div>
            <p className="note" style={{ marginTop: 12 }}>Recipients receive the table as CSV only if they may see it here; others are skipped and noted in the run.
              A graph needs more than one kept run. Column names are those of the table's download.</p>
            {err && <p className="error-line">{err}</p>}
            <div className="form-row" style={{ marginTop: 12 }}>
              <button className="btn" type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
              <Link className="btn btn-ghost" to={id === null ? "/reports" : `/reports/${id}`}>Cancel</Link>
            </div>
          </form>
        </div>
      </section>
    </>
  );
}
