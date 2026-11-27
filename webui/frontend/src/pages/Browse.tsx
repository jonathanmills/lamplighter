// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Browse (paths.view_all): walk the tree with subtree size, files, last
// read and cold share per directory (from the collector's rollups), then
// live listings below the rollup depth. Every directory has its own URL.
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError, type BrowseResult, type DirTotals, type LiveDir } from "../api";
import { ago, bytes, int } from "../format";
import SortTable, { type Column } from "../components/SortTable";
import { ShareBar, sharePct } from "./Users";

const now = () => Math.floor(Date.now() / 1000);
const lastRead = (t: number | null | undefined) => (t ? ago(now() - t) : "—");
const isLive = (d: DirTotals | LiveDir): d is LiveDir => "live" in d;
const dirLink = (id: string) => `/browse?dir=${encodeURIComponent(id)}`;

export default function Browse() {
  const [params] = useSearchParams();
  const dir = params.get("dir") ?? undefined;
  const [r, setR] = useState<BrowseResult | null>(null);
  const [err, setErr] = useState<{ status: number; msg: string } | null>(null);

  useEffect(() => {
    setErr(null);
    api.browse(dir).then((x) => { setR(x); document.title = `${x.dir.path} · Browse · Lamplighter`; })
      .catch((e) => setErr({ status: e instanceof ApiError ? e.status : 0, msg: String(e.message ?? e) }));
  }, [dir]);

  const rolled = useMemo(() => (r?.subdirs ?? []).filter((d): d is DirTotals => !isLive(d)), [r]);
  const live = useMemo(() => (r?.subdirs ?? []).filter(isLive), [r]);

  if (err) {
    return <section className="panel"><div className="empty">
      <h2>{err.status === 404 ? "Nothing to browse yet" : "This directory could not load"}</h2>
      <p className="muted">{err.msg}</p>
      {dir && <Link to="/browse">Back to the top</Link>}
    </div></section>;
  }
  if (!r) return <div className="page-head"><div><h1>Browse</h1><p>Loading…</p></div></div>;

  const t = r.dir.totals;
  const dirCols: Column<DirTotals>[] = [
    { key: "name", title: "Directory", align: "left", value: (d) => d.name, render: (d) => <Link to={dirLink(d.id)}>{d.name}/</Link> },
    { key: "owner", title: "Mostly owned by", align: "left", value: (d) => d.top_owner,
      render: (d) => d.top_owner ? <Link to={`/usage/user/${encodeURIComponent(d.top_owner)}`}><code>{d.top_owner}</code></Link> : <span className="muted">—</span> },
    { key: "files", title: "Files", value: (d) => d.files, render: (d) => int(d.files) },
    { key: "bytes", title: "Volume", value: (d) => d.bytes, render: (d) => bytes(d.bytes) },
    { key: "share", title: "Share of this directory", value: (d) => d.bytes, render: (d) => <ShareBar part={d.bytes} whole={t?.bytes ?? 0} /> },
    { key: "cold", title: "Not read in 1 year", value: (d) => (d.bytes ? d.cold_1y_bytes / d.bytes : 0),
      render: (d) => <ShareBar part={d.cold_1y_bytes} whole={d.bytes} /> },
    { key: "read", title: "Last read", value: (d) => d.newest_access ?? 0, render: (d) => lastRead(d.newest_access) },
  ];

  return (
    <>
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <h1 style={{ overflowWrap: "anywhere" }}><code>{r.dir.path}</code></h1>
          <nav aria-label="Path" className="crumbs" style={{ flexWrap: "wrap", whiteSpace: "normal" }}>
            {r.ancestors.map((a) => (
              <span key={a.id} style={{ display: "inline-flex", gap: 8 }}>
                <Link to={a.depth === 0 ? "/browse" : dirLink(a.id)}>{a.depth === 0 ? "top" : a.name}</Link><span className="sep">/</span>
              </span>
            ))}
            <b>{r.dir.depth === 0 ? "top" : r.dir.path.split("/").pop()}</b>
          </nav>
        </div>
      </div>

      {t ? (
        <section className="stats stats-4" aria-label="This directory">
          <div className="stat"><span className="stat-label">Volume</span><span className="stat-value">{bytes(t.bytes)}</span><span className="stat-sub">{int(t.subdirs)} sub-directories</span></div>
          <div className="stat"><span className="stat-label">Files</span><span className="stat-value">{int(t.files)}</span><span className="stat-sub">in the whole subtree</span></div>
          <div className="stat"><span className="stat-label">Last read</span><span className="stat-value">{lastRead(t.newest_access)}</span><span className="stat-sub">most recent access in the subtree</span></div>
          <div className="stat"><span className="stat-label">Not read in 1 year</span><span className="stat-value">{sharePct(t.cold_1y_bytes, t.bytes)}</span><span className="stat-sub">{bytes(t.cold_1y_bytes)} · {bytes(t.cold_3y_bytes)} over 3 years</span></div>
        </section>
      ) : (
        <p className="note">Below the summary depth ({r.depth_limit} levels): entries are listed live, without subtree totals. The totals of the nearest summarised directory above include everything here.</p>
      )}

      {rolled.length > 0 && (
        <section className="panel" aria-label="Sub-directories">
          <div className="panel-head"><h2>Sub-directories</h2><span className="meta">as of {ago(now() - r.as_of)}</span></div>
          <div className="panel-body">
            <SortTable rows={rolled} columns={dirCols} initialSort="bytes" rowKey={(d) => d.id} empty="No sub-directories." />
          </div>
        </section>
      )}

      {live.length > 0 && (
        <section className="panel" aria-label="Sub-directories (live)">
          <div className="panel-head"><h2>Sub-directories</h2><span className="meta">live listing · {live.length}{live.length >= 500 ? "+" : ""}</span></div>
          <div className="panel-body" style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead><tr><th style={{ textAlign: "left" }}>Directory</th><th style={{ textAlign: "left" }}>Owner</th><th>Last read</th></tr></thead>
              <tbody>{live.map((d) => (
                <tr key={d.id}><td style={{ textAlign: "left" }}><Link to={dirLink(d.id)}>{d.name}/</Link></td>
                  <td style={{ textAlign: "left" }}><code>{d.owner ?? "—"}</code></td><td>{lastRead(d.newest_access)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      )}

      {r.files.count > 0 && (
        <section className="panel" aria-label="Files">
          <div className="panel-head"><h2>Files in this directory</h2>
            <span className="meta">{r.files.shown < r.files.count ? `largest ${r.files.shown} of ${int(r.files.count)}` : `${int(r.files.count)} files`}</span></div>
          <div className="panel-body" style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead><tr><th style={{ textAlign: "left" }}>Name</th><th style={{ textAlign: "left" }}>Owner</th><th>Size</th><th>Last read</th><th>Last modified</th></tr></thead>
              <tbody>{r.files.items.map((f) => (
                <tr key={f.name}><td style={{ textAlign: "left" }}><code>{f.name}</code></td>
                  <td style={{ textAlign: "left" }}><code>{f.owner ?? "—"}</code></td><td>{bytes(f.size)}</td>
                  <td>{lastRead(f.last_access)}</td><td>{lastRead(f.last_mod)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
