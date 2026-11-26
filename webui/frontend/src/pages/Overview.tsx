// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Overview: Robinhood's status and the shape of the filesystem, from
// cheap reads only (VARS and ACCT_STAT; never a scan of ENTRIES).
import { useEffect, useMemo, useState } from "react";
import { api, type Status, type UsageSummary } from "../api";
import { ago, bytes, dateTime, duration, int } from "../format";
import BarChart from "../components/BarChart";
import Panel from "../components/Panel";

const RAMP = ["--ramp-1", "--ramp-2", "--ramp-3", "--ramp-4"];
const fmtFiles = (v: number) => int(v);

export default function Overview() {
  const [status, setStatus] = useState<Status | null>(null);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.status(), api.usage()])
      .then(([s, u]) => { setStatus(s); setUsage(u); })
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  const owners = useMemo(() => (usage?.top_users ?? []).filter((u) => u.bytes > 0).slice(0, 8), [usage]);
  const ownerNames = useMemo(() => owners.map((o) => o.user), [owners]);
  const ownerBytes = useMemo(() => owners.map((o) => o.bytes), [owners]);
  const sizeBuckets = useMemo(() => (usage?.file_sizes ?? []).map((b) => b.bucket), [usage]);
  const sizeFiles = useMemo(() => (usage?.file_sizes ?? []).map((b) => b.files), [usage]);

  if (error) {
    return <section className="panel"><div className="empty"><h2>The overview could not load</h2><p className="error-line">{error}</p></div></section>;
  }
  if (!status || !usage) return <PageHead fs={null} />;

  const scan = status.last_scan;
  const files = usage.by_type.file ?? { entries: 0, bytes: 0 };
  const dirs = usage.by_type.dir ?? { entries: 0, bytes: 0 };
  const total = Object.values(usage.by_type).reduce((a, t) => a + t.bytes, 0);
  const scanTook = scan.started !== undefined && scan.ended !== undefined ? scan.ended - scan.started : undefined;
  const done = scan.status === "done";

  return (
    <>
      <PageHead fs={status.filesystem} />

      <section className="stats" aria-label="At a glance">
        <Stat label="Files" value={int(files.entries)} sub={bytes(files.bytes)} />
        <Stat label="Directories" value={int(dirs.entries)} sub="in the namespace" />
        <Stat label="Volume" value={bytes(total)} sub="logical size" />
        <Stat label="Last scan"
              value={<span className="badge">
                <span className="badge-dot" style={{ background: done ? "var(--ok)" : "var(--warn)" }} aria-hidden="true">{done ? "✓" : "!"}</span>
                {done ? "Completed" : (scan.status ?? "Unknown")}
              </span>}
              sub={ago(scan.age_seconds)} title={`Finished ${dateTime(scan.ended)}`} />
        <Stat label="Scan duration" value={duration(scanTook)}
              sub={scan.interval_seconds ? `every ${duration(scan.interval_seconds)}` : "interval not set"} />
        <Stat label="Scan errors" value={int(scan.errors ?? 0)}
              sub={`${int(scan.entries ?? 0)} entries scanned`} />
      </section>

      <div className="grid">
        <Panel span={7} title="Largest owners by volume" meta="logical size"
               table={{ columns: ["Owner", "Volume", "Entries"], rows: owners.map((o) => [o.user, bytes(o.bytes), int(o.entries)]) }}>
          <BarChart horizontal binary categories={ownerNames} values={ownerBytes} colorTokens="--chart-1"
                    format={bytes} ariaLabel={"Volume by owner: " + owners.map((o) => `${o.user} ${bytes(o.bytes)}`).join(", ")} />
        </Panel>
        <Panel span={5} title="Files by size" meta="file count"
               table={{ columns: ["Size", "Files"], rows: usage.file_sizes.map((b) => [b.bucket, int(b.files)]) }}>
          <BarChart categories={sizeBuckets} values={sizeFiles} colorTokens={RAMP}
                    format={fmtFiles} ariaLabel={"Files by size: " + usage.file_sizes.map((b) => `${b.bucket} ${int(b.files)}`).join(", ")} />
        </Panel>
      </div>
    </>
  );
}

function PageHead({ fs }: { fs: string | null }) {
  return (
    <div className="page-head">
      <div>
        <h1>Overview</h1>
        <p>{fs ? <>Filesystem <code>{fs}</code></> : "Loading…"}</p>
      </div>
    </div>
  );
}

function Stat({ label, value, sub, title }: { label: string; value: React.ReactNode; sub: string; title?: string }) {
  return (
    <div className="stat" title={title}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      <span className="stat-sub">{sub}</span>
    </div>
  );
}
