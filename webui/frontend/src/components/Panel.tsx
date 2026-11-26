// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
import { useState, type ReactNode } from "react";

interface Props {
  title: string;
  meta?: ReactNode;
  span: 4 | 5 | 6 | 7 | 8 | 12;
  children: ReactNode;
  /** When given, the panel offers a Chart/Table switch (accessible data view). */
  table?: { columns: string[]; rows: (string | number)[][] };
}

export default function Panel({ title, meta, span, children, table }: Props) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className={`panel span-${span}`} aria-label={title}>
      <div className="panel-head">
        <h2>{title}</h2>
        <span style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
          {meta && <span className="meta">{meta}</span>}
          {table && (
            <button type="button" className="btn btn-ghost" style={{ padding: "2px 8px", fontSize: 12 }}
                    onClick={() => setAsTable(!asTable)} aria-pressed={asTable}>
              {asTable ? "Chart" : "Table"}
            </button>
          )}
        </span>
      </div>
      <div className="panel-body">
        {asTable && table ? (
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead><tr>{table.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>{table.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{v}</td>)}</tr>)}</tbody>
            </table>
          </div>
        ) : children}
      </div>
    </section>
  );
}
