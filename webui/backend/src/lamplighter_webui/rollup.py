# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Per-directory subtree totals, computed mostly inside MariaDB.

Designed for billions of entries (measured with lamplighter-scale; see
doc/webui-design.md, Scale). The expensive part is unavoidable: every
file's directory lives in NAMES and its size and times in ENTRIES, so
each file must be joined once. Done row by row through random lookups,
that is about 30 times slower than a scan; done in id order, it is
close to a scan. So:

1. The root comes from VARS (RootId), as Robinhood records it.
2. The directory tree (directories only, about 1% of entries) goes into
   a TEMPORARY table, with each directory's depth and its tracked
   ancestor (itself down to ``depth``, else its ancestor at ``depth``),
   assigned level by level.
3. ONE pass over the files, joined in id order: ENTRIES (clustered by
   id) against NAMES keyed by id. On a replica with a covering index on
   NAMES (id, parent_id), the index is used directly; otherwise a
   TEMPORARY id-ordered copy of NAMES (id, parent_id) is built first.
   The pass groups by (tracked directory, owner) in memory
   (``work_mem``), which also gives each directory's top owner.
4. Python adds the totals up the tracked tree, bottom-up.

TEMPORARY tables work on a read-only replica (the user needs CREATE
TEMPORARY TABLES). Run it on one connection: they live on it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

YEAR = 365 * 86400
DIRS = "lamplighter_rollup_dirs"
BYID = "lamplighter_rollup_names"


def _s(v) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)


def _root(c: Connection):
    """The root's id, from VARS (RootId is stored as fsid/inode); the old
    full NAMES anti-join only as a fallback for databases without it."""
    rid = c.execute(text("SELECT value FROM VARS WHERE varname = 'RootId'")).scalar()
    if rid:
        rid = _s(rid).replace("/", ":")
        if c.execute(text("SELECT 1 FROM NAMES WHERE parent_id = :r LIMIT 1"), {"r": rid}).first():
            return rid.encode()
    row = c.execute(text(
        "SELECT parent_id, COUNT(*) AS n FROM NAMES WHERE parent_id NOT IN "
        "(SELECT id FROM NAMES WHERE id IS NOT NULL) GROUP BY parent_id ORDER BY n DESC LIMIT 1")).first()
    return row[0] if row else None


def covering_index(c: Connection) -> str | None:
    """A NAMES index whose first columns are (id, parent_id), if the
    database has one (an optional replica index; Robinhood creates none)."""
    rows = c.execute(text(
        "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'NAMES' ORDER BY INDEX_NAME, SEQ_IN_INDEX")).all()
    cols: dict[str, list[str]] = defaultdict(list)
    for name, _, col in rows:
        cols[_s(name)].append(_s(col))
    for name, c_ in cols.items():
        if c_[:2] == ["id", "parent_id"]:
            return name
    return None


def _parallel_file_pass(source: Engine, workers: int, setup, file_pass: str, params: dict) -> list:
    """The file pass over primary-key ranges, on ``workers`` connections; each
    folds its (directory, owner) totals into a server-side MEMORY table and
    returns it once (see parallel.map_ranges)."""
    from .parallel import map_ranges, where
    tmp = "lamplighter_rollup_acc"

    def start(c):
        if setup:
            setup(c)
        c.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {tmp}"))
        c.execute(text(f"CREATE TEMPORARY TABLE {tmp} (anc VARBINARY(64), uid VARBINARY(127), "
                       "files BIGINT UNSIGNED, bytes BIGINT UNSIGNED, newest INT UNSIGNED, oldest INT UNSIGNED, "
                       "c1 BIGINT UNSIGNED, c3 BIGINT UNSIGNED, UNIQUE KEY (anc, uid)) ENGINE=MEMORY"))

    def scan(c, r):
        cond, rp = where(r, "e.id")
        c.execute(text(
            f"INSERT INTO {tmp} (anc, uid, files, bytes, newest, oldest, c1, c3) "
            + file_pass.format(range=cond)
            + " ON DUPLICATE KEY UPDATE files = files + VALUES(files), bytes = bytes + VALUES(bytes), "
              "newest = GREATEST(COALESCE(newest, VALUES(newest)), COALESCE(VALUES(newest), newest)), "
              "oldest = LEAST(COALESCE(oldest, VALUES(oldest)), COALESCE(VALUES(oldest), oldest)), "
              "c1 = c1 + VALUES(c1), c3 = c3 + VALUES(c3)"), {**params, **rp})

    def finish(c):
        rows = c.execute(text(f"SELECT anc, uid, files, bytes, newest, oldest, c1, c3 FROM {tmp}")).all()
        c.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {tmp}"))
        return rows

    return [row for part in map_ranges(source, workers, scan, start, finish) for row in part]


def _add_owner(mix: dict, uid: str, st: list) -> None:
    cur = mix.get(uid)
    if cur is None:
        mix[uid] = list(st)
        return
    cur[0] += st[0]
    cur[1] += st[1]
    cur[2] = max(filter(None, [cur[2], st[2]]), default=None)
    cur[3] = min(filter(None, [cur[3], st[3]]), default=None)
    cur[4] += st[4]
    cur[5] += st[5]


def compute(c: Connection, now: int, depth: int, fs_path: str, work_mem: int = 2 * 1024**3,
            scratch: str | None = None, source: Engine | None = None, workers: int = 1,
            setup: Callable[[Connection], None] | None = None) -> list[dict]:
    """Per-directory subtree totals down to ``depth``.

    With ``scratch`` (a writable schema on the same server as Robinhood's
    database), ``source`` and ``workers`` > 1, the directory map (and the
    id-ordered NAMES copy, without a covering index) are ordinary tables in
    the scratch schema, shared by ``workers`` connections that join their
    primary-key ranges in parallel. Otherwise everything runs on ``c`` with
    TEMPORARY tables (works on a read-only replica).
    """
    root = _root(c)
    if root is None:
        return []
    # Group in memory: MariaDB's 16 MB default spills these GROUP BYs to disk.
    c.execute(text(f"SET SESSION tmp_table_size = {int(work_mem)}, max_heap_table_size = {int(work_mem)}"))
    shared = bool(scratch) and source is not None and workers > 1
    if shared:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", scratch):
            raise ValueError(f"invalid scratch schema name {scratch!r}")
        kind, dirs, byid = "TABLE", f"`{scratch}`.rollup_dirs", f"`{scratch}`.rollup_names"
    else:
        kind, dirs, byid = "TEMPORARY TABLE", DIRS, BYID

    # 2. the directory tree, with depth and tracked ancestor (root: depth 0)
    c.execute(text(f"DROP {kind} IF EXISTS {dirs}"))
    c.execute(text(
        f"CREATE {kind} {dirs} (id VARBINARY(64) PRIMARY KEY, parent_id VARBINARY(64), "
        "name VARBINARY(255), depth INT, anc VARBINARY(64), uid VARBINARY(127), KEY (depth)) ENGINE=InnoDB "
        "SELECT STRAIGHT_JOIN n.id, n.parent_id, n.name, NULL AS depth, NULL AS anc, e.uid "
        "FROM ENTRIES e JOIN NAMES n ON n.id = e.id WHERE e.type = 'dir'"))
    c.execute(text(f"INSERT IGNORE INTO {dirs} (id, parent_id, name, depth, anc) VALUES (:r, NULL, '', 0, :r)"),
              {"r": root})
    level = 0
    while c.execute(text(
            f"UPDATE {dirs} d JOIN {dirs} p ON p.id = d.parent_id "
            "SET d.depth = p.depth + 1, d.anc = IF(p.depth < :depth, d.id, p.anc) "
            "WHERE p.depth = :lvl AND d.depth IS NULL"), {"depth": depth, "lvl": level}).rowcount:
        level += 1
    if shared:
        c.commit()                     # the workers' connections must see the map

    tracked = c.execute(text(f"SELECT id, parent_id, name, depth, uid FROM {dirs} WHERE depth <= :d"),
                        {"d": depth}).all()

    # 3. one ordered pass over the files
    cover = covering_index(c)
    if cover:
        names = f"NAMES n FORCE INDEX (`{cover}`)"
    else:
        c.execute(text(f"DROP {kind} IF EXISTS {byid}"))
        c.execute(text(f"CREATE {kind} {byid} (id VARBINARY(64) PRIMARY KEY, parent_id VARBINARY(64)) "
                       "ENGINE=InnoDB SELECT id, parent_id FROM NAMES WHERE id IS NOT NULL ORDER BY id"))
        if shared:
            c.commit()
        names = f"{byid} n"
    params = {"c1": now - YEAR, "c3": now - 3 * YEAR}
    file_pass = (
        "SELECT STRAIGHT_JOIN d.anc, e.uid, COUNT(*), COALESCE(SUM(e.size), 0), MAX(e.last_access), "
        "MIN(e.last_access), COALESCE(SUM(IF(e.last_access < :c1, e.size, 0)), 0), "
        "COALESCE(SUM(IF(e.last_access < :c3, e.size, 0)), 0) "
        f"FROM ENTRIES e JOIN {names} ON n.id = e.id JOIN {dirs} d ON d.id = n.parent_id "
        "WHERE e.type = 'file' AND {range} GROUP BY d.anc, e.uid")
    try:
        if shared:
            rows = _parallel_file_pass(source, workers, setup, file_pass, params)
        else:
            rows = c.execute(text(file_pass.format(range="TRUE")), params).all()
    finally:
        c.execute(text(f"DROP {kind} IF EXISTS {dirs}"))
        c.execute(text(f"DROP {kind} IF EXISTS {byid}"))
        if shared:
            c.commit()

    # 4. assemble and roll up in Python (tracked directories only)
    node: dict[bytes, dict] = {}
    for i, par, name, d, uid in tracked:
        d = int(d)
        node[i] = {"dir_id": i, "parent_dir": par if d > 0 else None, "depth": d, "name": _s(name),
                   "owner": _s(uid) if uid is not None else None, "children": [],
                   "files": 0, "bytes": 0, "newest": None, "oldest": None, "cold1": 0, "cold3": 0,
                   "mix": {}}       # owner -> [files, bytes, newest, oldest, cold1, cold3]
    for n in node.values():
        if n["parent_dir"] in node:
            node[n["parent_dir"]]["children"].append(n["dir_id"])
    for anc, uid, files, size, newest, oldest, c1, c3 in rows:
        n = node.get(anc)
        if n is None:
            continue
        n["files"] += int(files)
        n["bytes"] += int(size)
        n["cold1"] += int(c1)
        n["cold3"] += int(c3)
        n["newest"] = max(filter(None, [n["newest"], newest]), default=None)
        n["oldest"] = min(filter(None, [n["oldest"], oldest]), default=None)
        _add_owner(n["mix"], _s(uid), [int(files), int(size), newest, oldest, int(c1), int(c3)])

    # bottom-up: deepest first, add each node into its parent
    for n in sorted(node.values(), key=lambda x: -x["depth"]):
        par = node.get(n["parent_dir"]) if n["parent_dir"] is not None else None
        if par is None:
            continue
        par["files"] += n["files"]
        par["bytes"] += n["bytes"]
        par["cold1"] += n["cold1"]
        par["cold3"] += n["cold3"]
        par["newest"] = max(filter(None, [par["newest"], n["newest"]]), default=None)
        par["oldest"] = min(filter(None, [par["oldest"], n["oldest"]]), default=None)
        for uid, st in n["mix"].items():
            _add_owner(par["mix"], uid, st)

    def path_of(n: dict) -> str:
        parts = []
        while n and n["depth"] > 0:
            parts.append(n["name"])
            n = node.get(n["parent_dir"])
        return (fs_path.rstrip("/") + "/" + "/".join(reversed(parts))) if parts else (fs_path or "/")

    out = []
    for n in node.values():
        top = max(((u, st[1]) for u, st in n["mix"].items()), key=lambda kv: kv[1], default=(None, 0))
        out.append({"dir_id": n["dir_id"], "parent_dir": n["parent_dir"], "depth": n["depth"],
                    "path": path_of(n)[:4096], "owner": n["owner"], "top_owner": top[0],
                    "top_owner_bytes": top[1], "subdirs": len(n["children"]), "files": n["files"],
                    "bytes": n["bytes"], "newest_access": n["newest"], "oldest_access": n["oldest"],
                    "cold_1y_bytes": n["cold1"], "cold_3y_bytes": n["cold3"],
                    # each owner's share of this subtree (own-files browsing)
                    "owners": [{"owner": u, "files": st[0], "bytes": st[1], "newest_access": st[2],
                                "oldest_access": st[3], "cold_1y_bytes": st[4], "cold_3y_bytes": st[5]}
                               for u, st in sorted(n["mix"].items())]})
    return out
