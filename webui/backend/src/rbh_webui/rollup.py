# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Per-directory subtree totals, computed mostly inside MariaDB.

1. A recursive query walks only the *directories* through the NAMES
   parent_id index and maps each one to its ancestor at ``depth``
   (itself, when shallower). The result goes in a TEMPORARY table, which
   works on a read-only replica (the user needs CREATE TEMPORARY TABLES).
2. One GROUP BY over the files gives totals per tracked directory, plus
   a second one per (directory, owner) for the top owner.
3. Python adds the totals up the tracked tree, bottom-up. Only the
   tracked directories (depth <= ``depth``) ever reach Python.

Run it on one connection: the temporary table lives on that connection.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.engine import Connection

YEAR = 365 * 86400


def _s(v) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)


def _root(c: Connection):
    """The root's id: a parent that is nobody's child (the one with most children)."""
    row = c.execute(text(
        "SELECT parent_id, COUNT(*) AS n FROM NAMES WHERE parent_id NOT IN "
        "(SELECT id FROM NAMES WHERE id IS NOT NULL) GROUP BY parent_id ORDER BY n DESC LIMIT 1")).first()
    return row[0] if row else None


def compute(c: Connection, now: int, depth: int, fs_path: str) -> list[dict]:
    root = _root(c)
    if root is None:
        return []
    c.execute(text("DROP TEMPORARY TABLE IF EXISTS rbh_webui_tree"))
    c.execute(text(
        "CREATE TEMPORARY TABLE rbh_webui_tree (id VARBINARY(64), parent_id VARBINARY(64), "
        "name VARBINARY(255), depth INT, anc VARBINARY(64), KEY (id), KEY (anc)) ENGINE=InnoDB "
        "WITH RECURSIVE tree AS ("
        "  SELECT n.id, n.parent_id, n.name, 1 AS depth, n.id AS anc"
        "    FROM NAMES n JOIN ENTRIES e ON e.id = n.id"
        "   WHERE e.type = 'dir' AND n.parent_id = :root"
        "  UNION ALL"
        "  SELECT n.id, n.parent_id, n.name, t.depth + 1, IF(t.depth < :depth, n.id, t.anc)"
        "    FROM tree t JOIN NAMES n ON n.parent_id = t.id"
        "    JOIN ENTRIES e ON e.id = n.id AND e.type = 'dir'"
        ") SELECT * FROM tree"), {"root": root, "depth": depth})

    # The tracked directories themselves (few): id, parent, name, depth, owner.
    tracked = c.execute(text(
        "SELECT t.id, t.parent_id, t.name, t.depth, e.uid FROM rbh_webui_tree t "
        "JOIN ENTRIES e ON e.id = t.id WHERE t.depth <= :depth"), {"depth": depth}).all()

    cold1, cold3 = now - YEAR, now - 3 * YEAR
    agg_sql = (
        "SELECT {key} AS k, {owner} COUNT(*), COALESCE(SUM(e.size), 0), MAX(e.last_access), "
        "MIN(e.last_access), COALESCE(SUM(IF(e.last_access < :c1, e.size, 0)), 0), "
        "COALESCE(SUM(IF(e.last_access < :c3, e.size, 0)), 0) "
        "FROM ENTRIES e JOIN NAMES n ON n.id = e.id {join} WHERE e.type = 'file' {where} "
        "GROUP BY k {group_owner}")
    p = {"c1": cold1, "c3": cold3, "root": root}
    # files under tracked directories, keyed by their tracked ancestor
    direct = c.execute(text(agg_sql.format(key="t.anc", owner="", join="JOIN rbh_webui_tree t ON t.id = n.parent_id",
                                           where="", group_owner="")), p).all()
    # files directly in the root
    direct += c.execute(text(agg_sql.format(key="n.parent_id", owner="", join="",
                                            where="AND n.parent_id = :root", group_owner="")), p).all()
    owners = c.execute(text(
        "SELECT t.anc, e.uid, COALESCE(SUM(e.size), 0) FROM ENTRIES e JOIN NAMES n ON n.id = e.id "
        "JOIN rbh_webui_tree t ON t.id = n.parent_id WHERE e.type = 'file' GROUP BY t.anc, e.uid")).all()
    c.execute(text("DROP TEMPORARY TABLE IF EXISTS rbh_webui_tree"))

    # --- assemble and roll up in Python (tracked directories only) ---
    node: dict[bytes, dict] = {root: {"dir_id": root, "parent_dir": None, "depth": 0, "name": "",
                                      "owner": None, "children": []}}
    for i, par, name, d, uid in tracked:
        node[i] = {"dir_id": i, "parent_dir": par, "depth": int(d), "name": _s(name),
                   "owner": _s(uid), "children": []}
    for n in node.values():
        n.update(files=0, bytes=0, newest=None, oldest=None, cold1=0, cold3=0, mix=defaultdict(int))
        if n["parent_dir"] in node:
            node[n["parent_dir"]]["children"].append(n["dir_id"])
    for k, files, size, newest, oldest, c1, c3 in direct:
        n = node.get(k)
        if n is None:
            continue
        n["files"] += int(files)
        n["bytes"] += int(size)
        n["cold1"] += int(c1)
        n["cold3"] += int(c3)
        n["newest"] = max(filter(None, [n["newest"], newest]), default=None)
        n["oldest"] = min(filter(None, [n["oldest"], oldest]), default=None)
    for k, uid, size in owners:
        if k in node:
            node[k]["mix"][_s(uid)] += int(size)

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

    rows = []
    for n in node.values():
        top = max(n["mix"].items(), key=lambda kv: kv[1], default=(None, 0))
        rows.append({"dir_id": n["dir_id"], "parent_dir": n["parent_dir"], "depth": n["depth"],
                     "path": path_of(n)[:4096], "owner": n["owner"], "top_owner": top[0],
                     "top_owner_bytes": top[1], "subdirs": len(n["children"]), "files": n["files"],
                     "bytes": n["bytes"], "newest_access": n["newest"], "oldest_access": n["oldest"],
                     "cold_1y_bytes": n["cold1"], "cold_3y_bytes": n["cold3"]})
    return rows
