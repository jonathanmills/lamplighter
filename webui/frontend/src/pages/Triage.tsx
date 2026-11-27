// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Triage: "the filesystem is slow, who is doing what?" Lustre jobstats
// (busiest jobs, targets, activity over time) next to what Robinhood
// knows (recently modified data, changelog). No job scheduler needed:
// job IDs are shown as Lustre labels them.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Triage as TriageData, type TriageSort, type TriageWindow } from "../api";
import { can, useMe } from "../App";
import { ago, bytes, bytesRate, int, rate } from "../format";
import LineChart from "../components/LineChart";
import Panel from "../components/Panel";

const WINDOWS: { key: TriageWindow; label: string; seconds: number }[] = [
  { key: "15m", label: "15 min", seconds: 900 },
  { key: "1h", label: "1 h", seconds: 3600 },
  { key: "6h", label: "6 h", seconds: 21600 },
  { key: "24h", label: "24 h", seconds: 86400 },
];
const SORTS: { key: TriageSort; label: string }[] = [
  { key: "md", label: "Metadata" },
  { key: "io", label: "Data" },
];
const REFRESH_MS = 60_000;

export default function Triage() {
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const window = (WINDOWS.find((w) => w.key === params.get("window"))?.key ?? "1h") as TriageWindow;
  const sort = (SORTS.find((s) => s.key === params.get("sort"))?.key ?? "md") as TriageSort;
  const seconds = WINDOWS.find((w) => w.key === window)!.seconds;
  const [data, setData] = useState<TriageData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    const load = () => api.triage(window, sort)
      .then((d) => { if (live) { setData(d); setError(""); } })
      .catch((e) => { if (live) setError(String(e.message ?? e)); });
    load();
    const timer = setInterval(load, REFRESH_MS);   // jobstats arrive every minute
    return () => { live = false; clearInterval(timer); };
  }, [window, sort]);

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next);
  };
  const js = data?.jobstats.configured ? data.jobstats : null;

  const points = useMemo(() => (js?.series ?? []).map((b) => ({
    at: b.t, value: sort === "md" ? b.md_ops / js!.step : (b.read_bytes + b.write_bytes) / js!.step,
  })), [js, sort]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Triage</h1>
          <p>Who is busy on the filesystem: Lustre jobstats next to what Robinhood knows.</p>
        </div>
        <div className="actions">
          <div className="segmented" role="group" aria-label="Rank jobs by">
            {SORTS.map((s) => (
              <button key={s.key} type="button" aria-pressed={s.key === sort} onClick={() => set("sort", s.key)}>{s.label}</button>
            ))}
          </div>
          <div className="segmented" role="group" aria-label="Time window">
            {WINDOWS.map((w) => (
              <button key={w.key} type="button" aria-pressed={w.key === window} onClick={() => set("window", w.key)}>{w.label}</button>
            ))}
          </div>
        </div>
      </div>

      {error && <section className="panel"><div className="empty"><h2>Triage could not load</h2><p className="error-line">{error}</p></div></section>}
      {!data && !error && <p className="muted">Loading…</p>}

      {data && (
        <div className="grid">
          {js ? (
            <>
              <section className="stats span-12" aria-label="Last window at a glance">
                <Stat label="Metadata requests" value={rate(js.totals.md_ops / seconds)} sub={`${int(js.totals.md_ops)} in ${WINDOWS.find((w) => w.key === window)!.label}`} />
                <Stat label="Read" value={bytesRate(js.totals.read_bytes / seconds)} sub={`${bytes(js.totals.read_bytes)} in total`} />
                <Stat label="Written" value={bytesRate(js.totals.write_bytes / seconds)} sub={`${bytes(js.totals.write_bytes)} in total`} />
                <Stat label="Active jobs" value={int(js.jobs.length)} sub={js.jobs.length === 25 ? "showing the top 25" : "with any activity"} />
                <Stat label="Lustre targets" value={int(js.targets.length)} sub="MDTs and OSTs with activity" />
                <Stat label="Sources" value={`${js.sources.filter((s) => !s.stale).length} of ${js.sources.length}`}
                      sub={js.as_of ? `newest data ${ago(js.now - js.as_of)}` : "no data yet"} />
              </section>

              <Panel span={12} title={sort === "md" ? "Metadata requests per second" : "Data read and written per second"}
                     meta={`all jobs · ${js.step / 60}-minute steps`}
                     table={{ columns: ["Time", sort === "md" ? "Requests/s" : "Bytes/s"],
                              rows: points.map((p) => [new Date(p.at * 1000).toLocaleTimeString(), sort === "md" ? rate(p.value) : bytesRate(p.value)]) }}>
                {points.length ? (
                  <LineChart points={points} format={sort === "md" ? rate : bytesRate} binary={sort === "io"}
                             ariaLabel={`${sort === "md" ? "Metadata requests" : "Data throughput"} per second over the last ${WINDOWS.find((w) => w.key === window)!.label}`} />
                ) : <p className="muted">No jobstats in this window.</p>}
              </Panel>

              <JobsTable jobs={js.jobs} seconds={seconds} sort={sort} linkUsers={can(me, "usage.view_aggregates")} />

              <section className="panel span-7" aria-label="Lustre targets">
                <div className="panel-head"><h2>Lustre targets</h2><span className="meta">busiest first</span></div>
                <div className="panel-body" style={{ overflowX: "auto" }}>
                  <TargetsTable targets={js.targets} seconds={seconds} sort={sort} />
                </div>
              </section>
              <section className="panel span-5" aria-label="Jobstats sources">
                <div className="panel-head"><h2>Jobstats sources</h2><span className="meta">{js.source === "spool" ? "spool directory" : "Prometheus"}</span></div>
                <div className="panel-body" style={{ overflowX: "auto" }}>
                  <table className="data-table">
                    <thead><tr><th style={{ textAlign: "left" }}>Source</th><th style={{ textAlign: "left" }}>Targets</th><th>Newest snapshot</th></tr></thead>
                    <tbody>{js.sources.map((s) => (
                      <tr key={s.source}>
                        <td style={{ textAlign: "left" }}><code>{s.source}</code></td>
                        <td style={{ textAlign: "left" }}>{s.targets.map((t) => t.replace(/^.*-/, "")).join(", ") || "—"}</td>
                        <td>{s.stale ? <span className="chip" title="No new snapshot recently: check this server's cron job and transport">
                              not reporting · {s.age_seconds === null ? "never" : ago(s.age_seconds)}</span>
                            : ago(s.age_seconds ?? undefined)}
                          {s.error && <div className="error-line" style={{ fontSize: 12 }}>{s.error}</div>}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </section>
            </>
          ) : <NotConfigured />}

          <RobinhoodSide data={data} />
        </div>
      )}
    </>
  );
}

function Stat({ label, value, sub }: { label: string; value: ReactNode; sub: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      <span className="stat-sub">{sub}</span>
    </div>
  );
}

function JobsTable({ jobs, seconds, sort, linkUsers }: {
  jobs: Extract<TriageData["jobstats"], { configured: true }>["jobs"]; seconds: number; sort: TriageSort; linkUsers: boolean;
}) {
  return (
    <section className="panel span-12" aria-label="Busiest jobs">
      <div className="panel-head"><h2>Busiest jobs</h2>
        <span className="meta">by {sort === "md" ? "metadata requests" : "data read and written"} · job IDs as Lustre labels them</span></div>
      <div className="panel-body" style={{ overflowX: "auto" }}>
        {!jobs.length ? <p className="muted">No job did any I/O in this window.</p> : (
          <table className="data-table">
            <thead><tr>
              <th style={{ textAlign: "left" }}>Job</th><th style={{ textAlign: "left" }}>Owner</th>
              <th>Metadata</th><th style={{ textAlign: "left" }}>Mostly</th><th>Read</th><th>Written</th><th>Targets</th>
            </tr></thead>
            <tbody>{jobs.map((j) => (
              <tr key={j.job_id}>
                <td style={{ textAlign: "left" }}><code>{j.job_id}</code></td>
                <td style={{ textAlign: "left" }}>{j.user
                  ? (linkUsers ? <Link to={`/usage/user/${encodeURIComponent(j.user)}`}>{j.name ?? j.user}</Link> : (j.name ?? j.user))
                  : <span className="muted">{j.parts.uid !== undefined ? `UID ${j.parts.uid}` : "—"}</span>}</td>
                <td title={`${int(j.md_ops)} requests`}>{rate(j.md_ops / seconds)}</td>
                <td style={{ textAlign: "left" }}><span className="chips">{j.top_ops.slice(0, 3).map(([op, n]) => (
                  <span key={op} className="chip" title={`${int(n)} ${op} requests`}>{op}</span>))}</span></td>
                <td title={bytes(j.read_bytes)}>{j.read_bytes ? bytesRate(j.read_bytes / seconds) : "—"}</td>
                <td title={bytes(j.write_bytes)}>{j.write_bytes ? bytesRate(j.write_bytes / seconds) : "—"}</td>
                <td>{j.targets}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </section>
  );
}

function TargetsTable({ targets, seconds, sort }: {
  targets: Extract<TriageData["jobstats"], { configured: true }>["targets"]; seconds: number; sort: TriageSort;
}) {
  const load = (t: (typeof targets)[number]) => (sort === "md" && t.target.includes("-MDT") ? t.md_ops : t.read_bytes + t.write_bytes + t.md_ops);
  const sorted = [...targets].sort((a, b) => load(b) - load(a));
  const max = Math.max(1, ...sorted.map(load));
  if (!sorted.length) return <p className="muted">No activity in this window.</p>;
  return (
    <table className="data-table">
      <thead><tr><th style={{ textAlign: "left" }}>Target</th><th>Jobs</th><th>Metadata</th><th>Read</th><th>Written</th><th>Share</th></tr></thead>
      <tbody>{sorted.map((t) => (
        <tr key={t.target}>
          <td style={{ textAlign: "left" }}><code>{t.target}</code></td>
          <td>{t.jobs}</td>
          <td>{t.md_ops ? rate(t.md_ops / seconds) : "—"}</td>
          <td>{t.read_bytes ? bytesRate(t.read_bytes / seconds) : "—"}</td>
          <td>{t.write_bytes ? bytesRate(t.write_bytes / seconds) : "—"}</td>
          <td><span className="bar-cell"><span className="share" aria-hidden="true"><span style={{ width: `${(load(t) / max) * 100}%` }} /></span></span></td>
        </tr>
      ))}</tbody>
    </table>
  );
}

function NotConfigured() {
  return (
    <section className="panel span-12" aria-label="Jobstats are not set up">
      <div className="panel-head"><h2>Jobstats are not set up</h2></div>
      <div className="panel-body">
        <p>Lustre keeps per-job I/O counters on its servers. To see the busiest jobs here, have each MDS and OSS write a
          snapshot every minute into this server's spool directory, for example with a cron job:</p>
        <pre className="cmd cmd-block">{"* * * * * root f=/path/to/spool/$(hostname -s).$(date +\\%s).jobstats; lctl get_param 'mdt.*.job_stats' 'obdfilter.*.job_stats' > $f.tmp 2>/dev/null && mv $f.tmp $f"}</pre>
        <p className="muted">Then enable the <code>jobstats</code> section of <code>config.yaml</code>. A site that already scrapes jobstats
          with Prometheus can use that instead. No job scheduler is needed: jobs are shown as Lustre labels them.</p>
      </div>
    </section>
  );
}

function RobinhoodSide({ data }: { data: TriageData }) {
  const rh = data.robinhood;
  const cl = rh.changelog;
  const now = Math.floor(Date.now() / 1000);
  return (
    <>
      <section className="panel span-6" aria-label="Recently modified data">
        <div className="panel-head"><h2>Recently modified data</h2>
          <span className="meta">{rh.recent ? `Robinhood · modified ${rh.recent.band} · as of ${ago(now - rh.recent.as_of)}` : "Robinhood"}</span></div>
        <div className="panel-body" style={{ overflowX: "auto" }}>
          {!rh.recent ? <p className="muted">No collector run yet.</p> : !rh.recent.owners.length ? <p className="muted">Nothing was modified recently.</p> : (
            <table className="data-table">
              <thead><tr><th style={{ textAlign: "left" }}>Owner</th><th style={{ textAlign: "left" }}>Name</th><th>Volume</th><th>Files</th></tr></thead>
              <tbody>{rh.recent.owners.map((o) => (
                <tr key={o.user}><td style={{ textAlign: "left" }}><code>{o.user}</code></td>
                  <td style={{ textAlign: "left" }}>{o.name ?? <span className="muted">—</span>}</td>
                  <td>{bytes(o.bytes)}</td><td>{int(o.files)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </div>
      </section>
      <section className="panel span-6" aria-label="Robinhood status">
        <div className="panel-head"><h2>Robinhood status</h2><span className="meta">changelog and scan</span></div>
        <div className="panel-body">
          <dl style={{ margin: 0 }}>
            <div className="dl-row"><dt>Changelog backlog</dt><dd>{cl?.backlog_records !== undefined ? `${int(cl.backlog_records)} records` : "changelogs not in use"}</dd></div>
            <div className="dl-row"><dt>Last record read</dt><dd>{cl?.last_read !== undefined ? int(cl.last_read) : "—"}</dd></div>
            <div className="dl-row"><dt>Last scan finished</dt><dd>{rh.last_scan?.age_seconds !== undefined ? ago(rh.last_scan.age_seconds) : "—"}</dd></div>
          </dl>
        </div>
      </section>
    </>
  );
}
