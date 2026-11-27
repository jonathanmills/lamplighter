// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Cold data, for data managers: who holds the most data nobody has read
// for a while, and how to reach them. Reads only the collector's output.
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError, toDisplayBands, type AgeBands, type ColdAge, type ColdOwners, type DirTotals } from "../api";
import { can, useMe } from "../App";
import { ago, bytes, int } from "../format";
import BarChart from "../components/BarChart";
import Panel from "../components/Panel";

const AGES: { key: ColdAge; label: string; phrase: string }[] = [
  { key: "90d", label: "90 days", phrase: "90 days" },
  { key: "1y", label: "1 year", phrase: "a year" },
  { key: "3y", label: "3 years", phrase: "3 years" },
];
const RAMP = ["--ramp-1", "--ramp-2", "--ramp-3", "--ramp-4"];

export default function ColdData() {
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const olderThan = (AGES.find((a) => a.key === params.get("older_than"))?.key ?? "1y") as ColdAge;
  const [data, setData] = useState<ColdOwners | null>(null);
  const [dirs, setDirs] = useState<DirTotals[] | null>(null);
  // Directory rollups track 1 and 3 years; the 90-day view shows the 1-year figures.
  const dirAge = olderThan === "3y" ? "3y" : "1y";
  const [age, setAge] = useState<AgeBands | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "none" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    setState("loading");
    Promise.all([api.coldOwners(olderThan), api.age()])
      .then(([o, a]) => { setData(o); setAge(a); setState("ok"); })
      .then(() => api.coldDirs(dirAge).then((d) => setDirs(d.directories)).catch(() => setDirs([])))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) { setState("none"); return; }
        setError(String(e.message ?? e)); setState("error");
      });
  }, [olderThan]);

  const top = useMemo(() => (data?.owners ?? []).slice(0, 8), [data]);
  const topNames = useMemo(() => top.map((o) => o.user), [top]);
  const topBytes = useMemo(() => top.map((o) => o.cold_bytes), [top]);
  const bands = useMemo(() => (age ? toDisplayBands(age.bands) : []), [age]);
  const bandLabels = useMemo(() => bands.map((b) => b.label), [bands]);
  const bandBytes = useMemo(() => bands.map((b) => b.bytes), [bands]);
  const phrase = AGES.find((a) => a.key === olderThan)!.phrase;
  const totalCold = (data?.owners ?? []).reduce((a, o) => a + o.cold_bytes, 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Cold data</h1>
          <p>Owners holding the most data that nobody has read in over {phrase}.</p>
        </div>
        <div className="actions">
          <div className="segmented" role="group" aria-label="Not accessed for at least">
            {AGES.map((a) => (
              <button key={a.key} type="button" aria-pressed={a.key === olderThan}
                      onClick={() => setParams({ older_than: a.key })}>{a.label}</button>
            ))}
          </div>
          {can(me, "cold.export") && state === "ok" && (
            <a className="btn" href={api.coldCsvUrl(olderThan)} download>Export CSV</a>
          )}
        </div>
      </div>

      {state === "none" && (
        <section className="panel"><div className="empty">
          <h2>No collector run yet</h2>
          <p className="muted">Cold-data figures come from the nightly collector. Run it once with <span className="cmd">lux-collector run</span>.</p>
        </div></section>
      )}
      {state === "error" && (
        <section className="panel"><div className="empty"><h2>Cold data could not load</h2><p className="error-line">{error}</p></div></section>
      )}

      {state === "ok" && data && (
        <div className="grid">
          <Panel span={7} title={`Not read in over ${phrase}`} meta={`${bytes(totalCold)} · as of ${ago(Math.floor(Date.now() / 1000) - data.as_of)}`}
                 table={{ columns: ["Owner", "Volume"], rows: top.map((o) => [o.user, bytes(o.cold_bytes)]) }}>
            {top.length ? (
              <BarChart horizontal binary categories={topNames} values={topBytes} colorTokens="--chart-1" format={bytes}
                        ariaLabel={`Data not read in over ${phrase}, by owner: ` + top.map((o) => `${o.user} ${bytes(o.cold_bytes)}`).join(", ")} />
            ) : <p className="muted">No data is that old.</p>}
          </Panel>
          <Panel span={5} title="Whole filesystem by last access" meta="volume"
                 table={{ columns: ["Last accessed", "Volume", "Files"], rows: bands.map((b) => [b.label, bytes(b.bytes), int(b.files)]) }}>
            <BarChart binary categories={bandLabels} values={bandBytes} colorTokens={RAMP} format={bytes}
                      ariaLabel={"Volume by time since last access: " + bands.map((b) => `${b.label} ${bytes(b.bytes)}`).join(", ")} />
          </Panel>
          <section className="panel span-12" aria-label="Coldest directories">
            <div className="panel-head"><h2>Coldest directories</h2>
              <span className="meta">datasets not read in over {dirAge === "3y" ? "3 years" : "1 year"}{olderThan === "90d" ? " (directory figures start at 1 year)" : ""}</span></div>
            <div className="panel-body" style={{ overflowX: "auto" }}>
              {!dirs ? <p className="muted">Loading…</p> : !dirs.length ? <p className="muted">No directory is that cold.</p> : (
                <table className="data-table">
                  <thead><tr><th style={{ textAlign: "left" }}>Directory</th><th style={{ textAlign: "left" }}>Mostly owned by</th>
                    <th>Not read</th><th>Share of the directory</th><th>Files</th><th>Last read</th></tr></thead>
                  <tbody>{dirs.slice(0, 10).map((d) => {
                    const cold = dirAge === "3y" ? d.cold_3y_bytes : d.cold_1y_bytes;
                    const share = d.bytes ? cold / d.bytes : 0;
                    return (
                      <tr key={d.id}>
                        <td style={{ textAlign: "left" }}>{can(me, "paths.view_all")
                          ? <Link to={`/browse?dir=${encodeURIComponent(d.id)}`}><code>{d.path}</code></Link> : <code>{d.path}</code>}</td>
                        <td style={{ textAlign: "left" }}><code>{d.top_owner ?? "—"}</code></td>
                        <td>{bytes(cold)}</td>
                        <td><span className="bar-cell">{Math.round(share * 100)}%<span className="share" aria-hidden="true"><span style={{ width: `${share * 100}%` }} /></span></span></td>
                        <td>{int(d.files)}</td>
                        <td>{d.newest_access ? ago(Math.floor(Date.now() / 1000) - d.newest_access) : "—"}</td>
                      </tr>
                    );
                  })}</tbody>
                </table>
              )}
            </div>
          </section>
          <section className="panel span-12" aria-label="Owners to contact">
            <div className="panel-head"><h2>Owners to contact</h2><span className="meta">{data.owners.length} {data.owners.length === 1 ? "owner" : "owners"}</span></div>
            <div className="panel-body" style={{ overflowX: "auto" }}>
              <table className="data-table">
                <thead><tr>
                  <th>Owner</th><th style={{ textAlign: "left" }}>Name</th><th style={{ textAlign: "left" }}>E-mail</th>
                  <th>Not read in {phrase}</th><th>Files</th><th>Share of their data</th>
                </tr></thead>
                <tbody>
                  {data.owners.map((o) => {
                    const share = o.total_bytes ? o.cold_bytes / o.total_bytes : 0;
                    return (
                      <tr key={o.user}>
                        <td><code>{o.user}</code></td>
                        <td style={{ textAlign: "left" }}>{o.name ?? <span className="muted">not in directory</span>}</td>
                        <td style={{ textAlign: "left" }}>{o.mail ? <><span>{o.mail}</span><CopyButton text={o.mail} /></> : <span className="muted">—</span>}</td>
                        <td>{bytes(o.cold_bytes)}</td>
                        <td>{int(o.cold_files)}</td>
                        <td><span className="bar-cell">{Math.round(share * 100)}%<span className="share" aria-hidden="true"><span style={{ width: `${share * 100}%` }} /></span></span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); }
    catch { window.getSelection()?.selectAllChildren(document.activeElement?.previousElementSibling ?? document.body); }
  };
  return <button type="button" className="copy" onClick={copy} aria-label={`Copy ${text}`}>{done ? "copied" : "copy"}</button>;
}
