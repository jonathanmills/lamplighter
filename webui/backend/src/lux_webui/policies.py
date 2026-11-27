# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Fileclass and policy-status counts, from Robinhood's ENTRIES table.

Robinhood stores, per entry:
- ``fileclass``: the fileclasses it matched, as "+class1+class2+"
  (an entry can belong to several);
- one ``<name>_status`` column per status manager instance, e.g.
  ``alert_status`` (alert/clear), ``lhsm_status`` (new, archived...) or
  ``checksum_status`` (ok/failed). Which ones exist depends on the
  site's configuration, so they are read from the table's structure.

The collector makes one GROUP BY pass (MariaDB aggregates; only summary
rows come back) and splits the class lists here. Alert policies used as
classifiers (doc/webui-design.md section 7) show up as counts of
"alert" per fileclass, for Checkmk to apply thresholds to.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

_STATUS_COLUMN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,59}_status$")
ALL = "*"            # fileclass value for "every entry"
UNCLASSIFIED = ""    # entries matching no fileclass
NO_STATUS = ""       # NULL / empty status: never evaluated by that policy


def status_columns(src: Connection) -> list[str]:
    """The policy status columns of ENTRIES, validated as identifiers."""
    cols = src.execute(text(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ENTRIES' AND COLUMN_NAME LIKE '%\\_status' "
        "ORDER BY ORDINAL_POSITION")).scalars().all()
    return [c for c in (_s(c) for c in cols) if _STATUS_COLUMN.match(c)]


def _s(v) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else ("" if v is None else str(v))


def split_classes(value) -> list[str]:
    """"+a+b+" -> ["a", "b"]; empty or "++" -> [UNCLASSIFIED]."""
    names = [c for c in _s(value).split("+") if c]
    return names or [UNCLASSIFIED]


def collect_counts(src: Connection) -> list[dict]:
    """Entries and file bytes per fileclass, per status and per both.

    Returns rows {fileclass, field, value, entries, bytes}:
      fileclass=<name>, field=""          totals per fileclass
      fileclass="*",    field=<column>    totals per status value
      fileclass=<name>, field=<column>    per fileclass and status value
    """
    cols = status_columns(src)
    keys = ", ".join(["fileclass"] + cols)
    rows = src.execute(text(
        f"SELECT {keys}, COUNT(*), COALESCE(SUM(CASE WHEN type = 'file' THEN size ELSE 0 END), 0) "
        f"FROM ENTRIES GROUP BY {keys}")).all()
    acc: dict[tuple[str, str, str], list[int]] = {}

    def add(fc: str, field: str, value: str, n: int, b: int) -> None:
        cell = acc.setdefault((fc, field, value), [0, 0])
        cell[0] += n
        cell[1] += b

    for row in rows:
        classes = split_classes(row[0])
        statuses = [(c, _s(v)[:128]) for c, v in zip(cols, row[1:1 + len(cols)])]
        n, b = int(row[-2]), int(row[-1])
        for fc in classes:
            add(fc[:255], "", "", n, b)
            for col, val in statuses:
                add(fc[:255], col, val, n, b)
        for col, val in statuses:
            add(ALL, col, val, n, b)
    return [{"fileclass": fc, "field": f, "value": v, "entries": n, "bytes": b}
            for (fc, f, v), (n, b) in sorted(acc.items())]
