// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// One user's footprint: totals, age, file sizes, history, and groups.
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, toDisplayBands, type UserDetail as Detail } from "../api";
import { ago, bytes, int } from "../format";
import BarChart from "../components/BarChart";
import LineChart from "../components/LineChart";
import Panel from "../components/Panel";
import { ShareBar, sharePct } from "./Users";

const RAMP = ["--ramp-1", "--ramp-2", "--ramp-3", "--ramp-4"];
const fmtFiles = (v: number) => int(v);


export default function UserDetail() {
  const { uid = "" } = useParams();
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<{ status: number; msg: string } | null>(null);

  useEffect(() => {
    document.title = `${uid} · Users & groups · Lamplighter`;
    setD(null); setErr(null);
    api.user(uid).then(setD).catch((e) => setErr({ status: e instanceof ApiError ? e.status : 0, msg: String(e.message ?? e) }));
  }, [uid]);

  const bands = useMemo(() => (d?.age ? toDisplayBands(d.age.bands) : []), [d]);
  const bandLabels = useMemo(() => bands.map((b) => b.label), [bands]);
  const bandBytes = useMemo(() => bands.map((b) => b.bytes), [bands]);
  const sizeLabels = useMemo(() => (d?.file_sizes ?? []).map((b) => b.bucket), [d]);
  const sizeFiles = useMemo(() => (d?.file_sizes ?? []).map((b) => b.files), [d]);
  // Snapshots, then today's live figure, so the line ends where the page's numbers are.
  const history = useMemo(() => d ? [...d.history.map((h) => ({ at: h.at, value: h.bytes })),
                                      { at: Math.floor(Date.now() / 1000), value: d.bytes }] : [], [d]);

  if (err) {
    return (
      <section className="panel"><div className="empty">
        <h2>{err.status === 403 ? "You can only see your own usage" : err.status === 404 ? `No data owned by ${uid}` : "This user could not load"}</h2>
        <p className="muted">{err.status === 403 ? "Seeing other users needs the usage.view_all_users permission." : err.msg}</p>
        <Link to="/usage">Back to Users & groups</Link>
      </div></section>
    );
  }
  if (!d) return <div className="page-head"><div><h1><code>{uid}</code></h1><p>Loading…</p></div></div>;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{d.name ?? d.user} <span className="muted" style={{ fontSize: 15 }}><code>{d.user}</code>{d.uid_number !== null ? ` · uid ${d.uid_number}` : ""}</span></h1>
          <p>{d.mail ?? "No e-mail in the directory"}</p>
        </div>
      </div>

      <section className="stats stats-4" aria-label="At a glance">
        <div className="stat"><span className="stat-label">Volume</span><span className="stat-value">{bytes(d.bytes)}</span>
          <span className="stat-sub">{sharePct(d.bytes, d.filesystem_bytes)} of the filesystem</span></div>
        <div className="stat"><span className="stat-label">Files</span><span className="stat-value">{int(d.files)}</span><span className="stat-sub">{int(d.entries)} entries in all</span></div>
        <div className="stat"><span className="stat-label">Directories</span><span className="stat-value">{int(d.dirs)}</span><span className="stat-sub">owned by {d.user}</span></div>
        <div className="stat"><span className="stat-label">Small files</span><span className="stat-value">{d.files ? Math.round((d.small_files / d.files) * 100) : 0}%</span>
          <span className="stat-sub">{int(d.small_files)} under 32 KiB</span></div>
      </section>

      <div className="grid">
        <Panel span={6} title="Data by last access" meta={d.age ? `as of ${ago(Math.floor(Date.now() / 1000) - d.age.as_of)}` : undefined}
               table={bands.length ? { columns: ["Last accessed", "Volume", "Files"], rows: bands.map((b) => [b.label, bytes(b.bytes), int(b.files)]) } : undefined}>
          {d.age ? <BarChart binary categories={bandLabels} values={bandBytes} colorTokens={RAMP} format={bytes}
                             ariaLabel={`${d.user}'s data by last access: ` + bands.map((b) => `${b.label} ${bytes(b.bytes)}`).join(", ")} />
                 : <p className="muted">Appears after the collector's first run.</p>}
        </Panel>
        <Panel span={6} title="Files by size" meta="file count"
               table={{ columns: ["Size", "Files"], rows: d.file_sizes.map((b) => [b.bucket, int(b.files)]) }}>
          <BarChart categories={sizeLabels} values={sizeFiles} colorTokens={RAMP} format={fmtFiles}
                    ariaLabel={`${d.user}'s files by size: ` + d.file_sizes.map((b) => `${b.bucket} ${int(b.files)}`).join(", ")} />
        </Panel>
        <Panel span={12} title="Volume over time" meta="collector snapshots, then now"
               table={{ columns: ["Snapshot", "Volume", "Entries"], rows: [...d.history.map((h) => [new Date(h.at * 1000).toLocaleString(), bytes(h.bytes), int(h.entries)]), ["Now (live)", bytes(d.bytes), int(d.entries)]] }}>
          {history.length >= 2
            ? <LineChart binary points={history} format={bytes} ariaLabel={`${d.user}'s volume over ${history.length} snapshots`} />
            : <p className="muted">A trend appears once the collector has taken a snapshot.</p>}
        </Panel>
        <section className="panel span-12" aria-label="By group">
          <div className="panel-head"><h2>Charged to groups</h2><span className="meta">by the files' group</span></div>
          <div className="panel-body">
            <table className="data-table">
              <thead><tr><th>Group</th><th>Entries</th><th>Volume</th><th>Share of their data</th></tr></thead>
              <tbody>{d.groups.map((g) => (
                <tr key={g.group}><td><code>{g.group}</code></td><td>{int(g.entries)}</td><td>{bytes(g.bytes)}</td>
                  <td><ShareBar part={g.bytes} whole={d.bytes} /></td></tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      </div>
    </>
  );
}
