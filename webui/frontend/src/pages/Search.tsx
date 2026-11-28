// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// File search, scoped to a directory tree. Robinhood's database indexes
// entries by id and parent only, so a search walks a subtree (like
// lux-find with a path) and stops at its limits, saying so. The search
// lives in the URL: bookmark or share it.
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type SearchResult } from "../api";
import { can, useMe } from "../App";
import { ago, bytes, int } from "../format";

const UNITS = [["KiB", 1024], ["MiB", 1024 ** 2], ["GiB", 1024 ** 3], ["TiB", 1024 ** 4]] as const;
const FIELDS = ["type", "owner", "group", "name", "min_size", "max_size", "not_accessed_days", "modified_within_days"] as const;

type Draft = { type: string; owner: string; group: string; name: string; minSize: string; maxSize: string;
  unit: string; notAccessed: string; modifiedWithin: string };

function fromParams(p: URLSearchParams): Draft {
  const unit = p.get("unit") ?? "GiB";
  const mult = UNITS.find(([u]) => u === unit)?.[1] ?? 1024 ** 3;
  const size = (k: string) => (p.get(k) ? String(Number(p.get(k)) / mult) : "");
  return { type: p.get("type") ?? "file", owner: p.get("owner") ?? "", group: p.get("group") ?? "",
    name: p.get("name") ?? "", minSize: size("min_size"), maxSize: size("max_size"), unit,
    notAccessed: p.get("not_accessed_days") ?? "", modifiedWithin: p.get("modified_within_days") ?? "" };
}

export default function Search() {
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const [draft, setDraft] = useState<Draft>(() => fromParams(params));
  const [data, setData] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // The API query: only the search fields (and the directory), from the URL.
  const query = useMemo(() => {
    const q = new URLSearchParams();
    if (params.get("dir")) q.set("dir", params.get("dir")!);
    for (const k of FIELDS) if (params.get(k)) q.set(k, params.get(k)!);
    return q.toString();
  }, [params]);
  const ran = params.get("go") === "1";

  useEffect(() => {
    setDraft(fromParams(params));
    if (!ran) { setData(null); return; }
    let live = true;
    setBusy(true); setError("");
    api.search(query).then((d) => { if (live) setData(d); })
      .catch((e) => { if (live) { setData(null); setError(String(e.message ?? e)); } })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [query, ran]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const mult = UNITS.find(([u]) => u === draft.unit)![1];
    const next = new URLSearchParams();
    for (const k of ["dir", "path"]) if (params.get(k)) next.set(k, params.get(k)!);
    const put = (k: string, v: string) => { if (v.trim() !== "") next.set(k, v.trim()); };
    put("type", draft.type); put("owner", draft.owner); put("group", draft.group); put("name", draft.name);
    if (draft.minSize) next.set("min_size", String(Math.round(Number(draft.minSize) * mult)));
    if (draft.maxSize) next.set("max_size", String(Math.round(Number(draft.maxSize) * mult)));
    if (draft.minSize || draft.maxSize) next.set("unit", draft.unit);
    put("not_accessed_days", draft.notAccessed); put("modified_within_days", draft.modifiedWithin);
    next.set("go", "1");
    setParams(next);
  };
  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setDraft({ ...draft, [k]: e.target.value });
  const where = params.get("path") ?? (data?.dir.path ?? "the whole filesystem");
  const now = Math.floor(Date.now() / 1000);

  return (
    <>
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <h1>Search</h1>
          <p style={{ overflowWrap: "anywhere" }}>Find files and directories under <code>{where}</code>
            {params.get("dir") && <> · <Link to="/search">search the whole filesystem</Link></>}</p>
        </div>
        {data && can(me, "cold.export") && data.results.length > 0 && (
          <div className="actions"><a className="btn btn-ghost" href={api.searchCsvUrl(query)} download>Export CSV</a></div>
        )}
      </div>

      <div className="grid">
        <section className="panel span-12" aria-label="Search criteria">
          <div className="panel-body">
            <form onSubmit={submit}>
              <div className="form-grid">
                <label className="field">Type
                  <select className="select" value={draft.type} onChange={set("type")}>
                    <option value="file">Files</option><option value="dir">Directories</option><option value="any">Anything</option>
                  </select></label>
                <label className="field">Owner (user name)<input className="search" value={draft.owner} onChange={set("owner")} placeholder="e.g. erin" /></label>
                <label className="field">Group<input className="search" value={draft.group} onChange={set("group")} placeholder="e.g. proj-climate" /></label>
                <label className="field">Name<input className="search" value={draft.name} onChange={set("name")} placeholder="e.g. *.h5 or run_??.log" /></label>
                <label className="field">At least
                  <span className="field-pair">
                    <input className="search" type="number" min="0" step="any" value={draft.minSize} onChange={set("minSize")} />
                    <select className="select" value={draft.unit} onChange={set("unit")} aria-label="Size unit">
                      {UNITS.map(([u]) => <option key={u}>{u}</option>)}</select></span></label>
                <label className="field">At most ({draft.unit})<input className="search" type="number" min="0" step="any" value={draft.maxSize} onChange={set("maxSize")} /></label>
                <label className="field">Not read for (days)<input className="search" type="number" min="0" value={draft.notAccessed} onChange={set("notAccessed")} /></label>
                <label className="field">Modified within (days)<input className="search" type="number" min="0" value={draft.modifiedWithin} onChange={set("modifiedWithin")} /></label>
              </div>
              <div className="form-row" style={{ marginTop: 14 }}>
                <button className="btn" type="submit" disabled={busy}>{busy ? "Searching…" : "Search"}</button>
                <span className="muted" style={{ fontSize: 12 }}>Searches walk the directory tree and stop at 1,000 matches,
                  20,000 directories or 20 seconds. Start from a smaller directory (Browse › Search here) to go further.</span>
              </div>
            </form>
          </div>
        </section>

        {error && <section className="panel span-12"><div className="empty"><h2>The search failed</h2><p className="error-line">{error}</p></div></section>}

        {data && (
          <section className="panel span-12" aria-label="Results">
            <div className="panel-head">
              <h2>{int(data.results.length)} {data.results.length === 1 ? "match" : "matches"}</h2>
              <span className="meta">
                {data.method === "index"
                  ? `${int(data.entries_examined ?? 0)} entries read from the filesystem-wide index, largest first, in ${data.elapsed_seconds} s`
                  : `${int(data.directories_searched)} directories searched in ${data.elapsed_seconds} s`}
                {data.complete ? " · complete" : ` · stopped at the ${data.stopped}: narrow the directory or the criteria`}</span>
            </div>
            <div className="panel-body" style={{ overflowX: "auto" }}>
              {!data.results.length ? <p className="muted">Nothing matches.</p> : (
                <table className="data-table">
                  <thead><tr><th style={{ textAlign: "left" }}>Path</th><th style={{ textAlign: "left" }}>Owner</th>
                    <th>Size</th><th>Last read</th><th>Modified</th></tr></thead>
                  <tbody>{data.results.map((r) => (
                    <tr key={r.path}>
                      <td style={{ textAlign: "left", overflowWrap: "anywhere", whiteSpace: "normal" }}><code>{r.path}{r.type === "dir" ? "/" : ""}</code></td>
                      <td style={{ textAlign: "left" }}>{can(me, "usage.view_aggregates")
                        ? <Link to={`/usage/user/${encodeURIComponent(r.owner)}`}><code>{r.owner}</code></Link> : <code>{r.owner}</code>}</td>
                      <td>{r.type === "file" ? bytes(r.size) : "—"}</td>
                      <td>{r.last_access ? ago(now - r.last_access) : "—"}</td>
                      <td>{r.last_mod ? ago(now - r.last_mod) : "—"}</td>
                    </tr>
                  ))}</tbody>
                </table>
              )}
            </div>
          </section>
        )}
      </div>
    </>
  );
}
