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
from sqlalchemy.engine import Connection, Engine

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


def _query(cols: list[str], where: str = "TRUE") -> str:
    keys = ", ".join(["fileclass"] + cols)
    return (f"SELECT {keys}, COUNT(*), COALESCE(SUM(CASE WHEN type = 'file' THEN size ELSE 0 END), 0) "
            f"FROM ENTRIES WHERE {where} GROUP BY {keys}")


def collect_counts(src: Connection) -> list[dict]:
    """Entries and file bytes per fileclass, per status and per both.

    Returns rows {fileclass, field, value, entries, bytes}:
      fileclass=<name>, field=""          totals per fileclass
      fileclass="*",    field=<column>    totals per status value
      fileclass=<name>, field=<column>    per fileclass and status value
    """
    cols = status_columns(src)
    return fold(src.execute(text(_query(cols))).all(), cols)


NARROW = 255   # group by LEFT(fileclass, NARROW): the column is VARBINARY(1023),
               # and MariaDB puts GROUP BYs on columns that wide in on-disk
               # temporary tables (3.5x slower, and contended in parallel)


def collect_counts_parallel(source: Engine, workers: int, setup=None) -> list[dict]:
    """collect_counts, with the scan split by primary-key range across
    ``workers`` connections (parallel.py). Each connection folds its chunks
    into a server-side table and returns it once. Fileclass lists are
    grouped on their first NARROW bytes; if any list is longer (rare), the
    counts are recomputed exactly with the wide grouping."""
    from .parallel import map_ranges, where
    with source.connect() as c:
        cols = status_columns(c)
    keys = ", ".join(["fileclass"] + cols)
    group = ", ".join(str(i) for i in range(1, len(cols) + 2))
    narrow = f"LEFT(fileclass, {NARROW})"
    select = ", ".join([narrow] + cols)
    select_as = ", ".join([narrow + " AS fileclass"] + cols)
    tmp = "lamplighter_class_acc"

    def start(c):
        if setup:
            setup(c)
        c.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {tmp}"))
        # key columns typed as in ENTRIES (status enums vary by site), counters explicit
        c.execute(text(f"CREATE TEMPORARY TABLE {tmp} ENGINE=MEMORY SELECT {select_as} FROM ENTRIES LIMIT 0"))
        c.execute(text(f"ALTER TABLE {tmp} ADD COLUMN n BIGINT UNSIGNED NOT NULL DEFAULT 0, "
                       "ADD COLUMN b BIGINT UNSIGNED NOT NULL DEFAULT 0, "
                       f"ADD COLUMN ov BIGINT UNSIGNED NOT NULL DEFAULT 0, ADD UNIQUE KEY ({keys})"))

    def scan(c, r):
        cond, params = where(r)
        c.execute(text(
            f"INSERT INTO {tmp} ({keys}, n, b, ov) SELECT {select}, COUNT(*), "
            "COALESCE(SUM(CASE WHEN type = 'file' THEN size ELSE 0 END), 0), "
            f"COALESCE(SUM(LENGTH(fileclass) > {NARROW}), 0) FROM ENTRIES WHERE {cond} GROUP BY {group} "
            "ON DUPLICATE KEY UPDATE n = n + VALUES(n), b = b + VALUES(b), ov = ov + VALUES(ov)"), params)

    def finish(c):
        rows = c.execute(text(f"SELECT {keys}, n, b, ov FROM {tmp}")).all()
        c.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {tmp}"))
        return rows

    rows = [row for part in map_ranges(source, workers, scan, start, finish) for row in part]
    if any(int(r[-1]) for r in rows):
        import logging
        logging.getLogger(__name__).info(
            "fileclass lists longer than %d bytes: counting with the exact (slower) grouping", NARROW)
        with source.connect() as c:
            if setup:
                setup(c)
            return collect_counts(c)
    return fold([r[:-1] for r in rows], cols)


def fold(rows, cols: list[str]) -> list[dict]:
    """Per-(fileclass list, statuses) rows -> per-class/status totals."""
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
