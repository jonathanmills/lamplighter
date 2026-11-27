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

from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.engine import Connection

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


def compute(c: Connection, now: int, depth: int, fs_path: str, work_mem: int = 2 * 1024**3) -> list[dict]:
    root = _root(c)
    if root is None:
        return []
    # Group in memory: MariaDB's 16 MB default spills these GROUP BYs to disk.
    c.execute(text(f"SET SESSION tmp_table_size = {int(work_mem)}, max_heap_table_size = {int(work_mem)}"))

    # 2. the directory tree, with depth and tracked ancestor (root: depth 0)
    c.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {DIRS}"))
    c.execute(text(
        f"CREATE TEMPORARY TABLE {DIRS} (id VARBINARY(64) PRIMARY KEY, parent_id VARBINARY(64), "
        "name VARBINARY(255), depth INT, anc VARBINARY(64), uid VARBINARY(127), KEY (depth)) ENGINE=InnoDB "
        "SELECT STRAIGHT_JOIN n.id, n.parent_id, n.name, NULL AS depth, NULL AS anc, e.uid "
        "FROM ENTRIES e JOIN NAMES n ON n.id = e.id WHERE e.type = 'dir'"))
    c.execute(text(f"INSERT IGNORE INTO {DIRS} (id, parent_id, name, depth, anc) VALUES (:r, NULL, '', 0, :r)"),
              {"r": root})
    level = 0
    while c.execute(text(
            f"UPDATE {DIRS} d JOIN {DIRS} p ON p.id = d.parent_id "
            "SET d.depth = p.depth + 1, d.anc = IF(p.depth < :depth, d.id, p.anc) "
            "WHERE p.depth = :lvl AND d.depth IS NULL"), {"depth": depth, "lvl": level}).rowcount:
        level += 1

    tracked = c.execute(text(f"SELECT id, parent_id, name, depth, uid FROM {DIRS} WHERE depth <= :d"),
                        {"d": depth}).all()

    # 3. one ordered pass over the files
    cover = covering_index(c)
    if cover:
        names = f"NAMES n FORCE INDEX (`{cover}`)"
    else:
        c.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {BYID}"))
        c.execute(text(f"CREATE TEMPORARY TABLE {BYID} (id VARBINARY(64) PRIMARY KEY, parent_id VARBINARY(64)) "
                       "ENGINE=InnoDB SELECT id, parent_id FROM NAMES WHERE id IS NOT NULL ORDER BY id"))
        names = f"{BYID} n"
    rows = c.execute(text(
        f"SELECT STRAIGHT_JOIN d.anc, e.uid, COUNT(*), COALESCE(SUM(e.size), 0), MAX(e.last_access), "
        "MIN(e.last_access), COALESCE(SUM(IF(e.last_access < :c1, e.size, 0)), 0), "
        "COALESCE(SUM(IF(e.last_access < :c3, e.size, 0)), 0) "
        f"FROM ENTRIES e JOIN {names} ON n.id = e.id JOIN {DIRS} d ON d.id = n.parent_id "
        "WHERE e.type = 'file' GROUP BY d.anc, e.uid"),
        {"c1": now - YEAR, "c3": now - 3 * YEAR}).all()
    c.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {DIRS}"))
    c.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {BYID}"))

    # 4. assemble and roll up in Python (tracked directories only)
    node: dict[bytes, dict] = {}
    for i, par, name, d, uid in tracked:
        d = int(d)
        node[i] = {"dir_id": i, "parent_dir": par if d > 0 else None, "depth": d, "name": _s(name),
                   "owner": _s(uid) if uid is not None else None, "children": [],
                   "files": 0, "bytes": 0, "newest": None, "oldest": None, "cold1": 0, "cold3": 0,
                   "mix": defaultdict(int)}
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
        n["mix"][_s(uid)] += int(size)

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
        for uid, b in n["mix"].items():
            par["mix"][uid] += b

    def path_of(n: dict) -> str:
        parts = []
        while n and n["depth"] > 0:
            parts.append(n["name"])
            n = node.get(n["parent_dir"])
        return (fs_path.rstrip("/") + "/" + "/".join(reversed(parts))) if parts else (fs_path or "/")

    out = []
    for n in node.values():
        top = max(n["mix"].items(), key=lambda kv: kv[1], default=(None, 0))
        out.append({"dir_id": n["dir_id"], "parent_dir": n["parent_dir"], "depth": n["depth"],
                    "path": path_of(n)[:4096], "owner": n["owner"], "top_owner": top[0],
                    "top_owner_bytes": top[1], "subdirs": len(n["children"]), "files": n["files"],
                    "bytes": n["bytes"], "newest_access": n["newest"], "oldest_access": n["oldest"],
                    "cold_1y_bytes": n["cold1"], "cold_3y_bytes": n["cold3"]})
    return out
