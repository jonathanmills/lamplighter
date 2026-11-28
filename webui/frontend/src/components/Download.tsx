// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// "Download CSV · JSON" for a table: the same API endpoint with
// ?format=csv|json (export.py). The server checks permissions and audits
// every export; show this only where the person may export.

type Params = Record<string, string | number | undefined | null>;

export const exportUrl = (path: string, params: Params, format: "csv" | "json") => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  q.set("format", format);
  return `/api/v1${path}?${q}`;
};

export default function Download({ path, params = {}, label = "this table" }: { path: string; params?: Params; label?: string }) {
  return (
    <span className="download" role="group" aria-label={`Download ${label}`}>
      Download <a href={exportUrl(path, params, "csv")} download>CSV</a>
      <span aria-hidden="true"> · </span>
      <a href={exportUrl(path, params, "json")} download>JSON</a>
    </span>
  );
}
