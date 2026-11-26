// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
// Dense sortable table (Checkmk-style). Click a header to sort; click
// again to reverse. Numbers sort numerically, text alphabetically.
import { useMemo, useState, type ReactNode } from "react";

export interface Column<T> {
  key: string;
  title: string;
  value: (row: T) => number | string | null;   // used for sorting
  render?: (row: T) => ReactNode;
  align?: "left" | "right";
}

export default function SortTable<T>({ rows, columns, initialSort, rowKey, empty }: {
  rows: T[]; columns: Column<T>[]; initialSort: string; rowKey: (row: T) => string; empty: string;
}) {
  const [sort, setSort] = useState<{ key: string; desc: boolean }>({ key: initialSort, desc: true });
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort.key) ?? columns[0];
    const out = [...rows].sort((a, b) => {
      const x = col.value(a), y = col.value(b);
      if (x === y) return 0;
      if (x === null) return 1;            // unknowns always last
      if (y === null) return -1;
      const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      return sort.desc ? -cmp : cmp;
    });
    return out;
  }, [rows, columns, sort]);

  if (!rows.length) return <p className="muted">{empty}</p>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="data-table">
        <thead><tr>
          {columns.map((c) => {
            const active = c.key === sort.key;
            return (
              <th key={c.key} style={{ textAlign: c.align ?? "right" }}
                  aria-sort={active ? (sort.desc ? "descending" : "ascending") : "none"}>
                <button type="button" className="th-sort"
                        onClick={() => setSort({ key: c.key, desc: active ? !sort.desc : typeof c.value(rows[0]) === "number" })}>
                  {c.title}{active ? (sort.desc ? " ↓" : " ↑") : ""}
                </button>
              </th>
            );
          })}
        </tr></thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={rowKey(r)}>
              {columns.map((c) => <td key={c.key} style={{ textAlign: c.align ?? "right" }}>{c.render ? c.render(r) : c.value(r)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
