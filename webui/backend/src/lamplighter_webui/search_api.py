# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""File search, scoped to a directory tree.

Robinhood indexes ENTRIES by id only (and NAMES by parent_id), and
Lamplighter never changes that schema. A whole-table search by owner,
size or age would read every row, so searches walk a directory subtree
instead: level by level through NAMES.parent_id (indexed), joined to
ENTRIES by id (primary key), with the filters applied by MariaDB. Like
lux-find with a path argument.

Every search is bounded (directories visited, matches, time) and says
whether it was complete; narrow the directory to search further.

Whole-filesystem searches by owner, group or minimum size can instead
read an optional index on a read replica (lamplighter-config
replica_indexes; any index on ENTRIES starting with (uid, size),
(gid, size) or (size) qualifies). The index is read biggest first, in
bounded batches, so the largest matches come first and the same limits
apply.
"""

from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import bindparam, text

from .acl import Perm
from .audit import audit
from .auth import Principal, require

router = APIRouter(prefix="/api/v1")

MAX_DIRS = 20_000        # directories visited per search
MAX_RESULTS = 1_000      # matches returned
TIME_BUDGET = 20.0       # seconds per search
BATCH = 500              # parent ids per query
INDEX_BATCH = 5_000      # index entries per query (indexed search)
DAY = 86400


def _s(v) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else ("" if v is None else str(v))


def glob_to_like(pattern: str) -> str:
    """Shell-style * and ? to SQL LIKE, with LIKE's own wildcards escaped."""
    out = []
    for ch in pattern:
        if ch in "%_\\":
            out.append("\\" + ch)
        elif ch == "*":
            out.append("%")
        elif ch == "?":
            out.append("_")
        else:
            out.append(ch)
    return "".join(out)


@dataclass
class Criteria:
    type: Literal["file", "dir", "any"] = "file"
    owner: str | None = None
    group: str | None = None
    name: str | None = None
    min_size: int | None = None
    max_size: int | None = None
    not_accessed_days: int | None = None
    modified_within_days: int | None = None

    def sql(self, now: int) -> tuple[str, dict]:
        """The filter for matching entries (directories are always walked)."""
        conds, params = [], {}
        if self.type != "any":
            conds.append("e.type = :type")
            params["type"] = self.type
        if self.owner:
            conds.append("e.uid = :owner")
            params["owner"] = self.owner
        if self.group:
            conds.append("e.gid = :group")
            params["group"] = self.group
        if self.name:
            conds.append("n.name LIKE :name")
            params["name"] = glob_to_like(self.name)
        if self.min_size is not None:
            conds.append("e.size >= :min_size")
            params["min_size"] = self.min_size
        if self.max_size is not None:
            conds.append("e.size <= :max_size")
            params["max_size"] = self.max_size
        if self.not_accessed_days is not None:
            conds.append("e.last_access < :acc")
            params["acc"] = now - self.not_accessed_days * DAY
        if self.modified_within_days is not None:
            conds.append("e.last_mod >= :mod")
            params["mod"] = now - self.modified_within_days * DAY
        return (" AND ".join(conds) or "TRUE"), params

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _root(c, store) -> bytes:
    with store.connect() as sc:
        row = sc.execute(text("SELECT r.dir_id FROM dir_rollup r JOIN collector_runs c ON c.id = r.run_id "
                              "WHERE c.status = 'ok' AND r.depth = 0 ORDER BY r.run_id DESC LIMIT 1")).first()
    if row is None:
        raise HTTPException(404, "No collector run yet: run lamplighter-collector once.")
    return row[0]


def path_of(c, dir_id: bytes) -> str:
    """Absolute path of a directory, walking up NAMES (indexed by id)."""
    parts, walk = [], dir_id
    for _ in range(1024):
        r = c.execute(text("SELECT parent_id, name FROM NAMES WHERE id = :d LIMIT 1"), {"d": walk}).first()
        if r is None:                       # the filesystem root has no NAMES row
            root = _s(c.execute(text("SELECT value FROM VARS WHERE varname = 'FS_Path'")).scalar()) or "/"
            return root.rstrip("/") + "".join("/" + p for p in reversed(parts)) if parts else root
        parts.append(_s(r.name))
        walk = r.parent_id
    raise HTTPException(500, "Directory tree too deep.")


def entry_indexes(c) -> dict[tuple[str, ...], str]:
    """Indexes on ENTRIES by their leading columns: {("uid", "size"): name, ...}."""
    cols: dict[str, list[str]] = {}
    for name, col in c.execute(text(
            "SELECT INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ENTRIES' AND INDEX_NAME <> 'PRIMARY' "
            "ORDER BY INDEX_NAME, SEQ_IN_INDEX")):
        cols.setdefault(_s(name), []).append(_s(col).lower())
    out: dict[tuple[str, ...], str] = {}
    for name, cs in sorted(cols.items()):
        for n in (1, 2):
            out.setdefault(tuple(cs[:n]), name)
    return out


def _index_plan(c, crit: Criteria) -> tuple[str, str, dict] | None:
    """(index, leading condition, params) for a whole-filesystem search, or None."""
    have = entry_indexes(c)
    if crit.owner and ("uid", "size") in have:
        return have[("uid", "size")], "e.uid = :lead", {"lead": crit.owner}
    if crit.group and ("gid", "size") in have:
        return have[("gid", "size")], "e.gid = :lead", {"lead": crit.group}
    if crit.min_size is not None and ("size",) in have:
        return have[("size",)], "e.size >= :lead", {"lead": crit.min_size}
    return None


def _glob_re(pattern: str) -> re.Pattern:
    """The same matching as glob_to_like + LIKE on a binary column."""
    return re.compile("".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern),
                      re.DOTALL)


def _indexed(c, start, paths, crit: Criteria, plan, t0: float) -> tuple[list[dict], str | None, int]:
    """Read the index biggest first, INDEX_BATCH entries at a time (keyset
    on size, id), applying the other criteria; returns matches, why it
    stopped (None: complete) and entries examined."""
    index, lead, lead_params = plan
    crit_no_name = Criteria(**{**crit.__dict__, "name": None})
    where, params = crit_no_name.sql(int(time.time()))
    name_re = _glob_re(crit.name.encode("utf-8", "surrogateescape").decode("latin-1")) if crit.name else None
    query = text(
        "SELECT e.id, e.type, e.uid, e.gid, e.size, e.last_access, e.last_mod, "
        f"({where}) AS hit FROM ENTRIES e FORCE INDEX (`{index}`) WHERE {lead} "
        "AND (:first OR e.size < :s OR (e.size = :s AND e.id < :i)) "
        "ORDER BY e.size DESC, e.id DESC LIMIT :n")
    names_q = text("SELECT id, parent_id, name FROM NAMES WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True))
    results: list[dict] = []
    examined, cursor = 0, None
    while True:
        rows = c.execute(query, {**params, **lead_params, "n": INDEX_BATCH, "first": cursor is None,
                                 "s": cursor[0] if cursor else 0, "i": cursor[1] if cursor else b""}).all()
        examined += len(rows)
        hits = [r for r in rows if r.hit]
        if hits:
            names: dict[bytes, list] = {}
            for n in c.execute(names_q, {"ids": [r.id for r in hits]}):
                names.setdefault(n.id, []).append(n)
            for r in hits:
                for n in names.get(r.id, []):             # one result per name (hard links)
                    if name_re and not name_re.fullmatch(bytes(n.name).decode("latin-1")):
                        continue
                    if n.parent_id not in paths:
                        paths[n.parent_id] = path_of(c, n.parent_id)
                    results.append({"path": paths[n.parent_id].rstrip("/") + "/" + _s(n.name),
                                    "type": _s(r.type), "owner": _s(r.uid), "group": _s(r.gid),
                                    "size": int(r.size or 0), "last_access": r.last_access,
                                    "last_mod": r.last_mod})
        if len(results) >= MAX_RESULTS:
            return results, "result limit", examined
        if len(rows) < INDEX_BATCH:
            return results, None, examined
        if time.monotonic() - t0 > TIME_BUDGET:
            return results, "time limit", examined
        cursor = (rows[-1].size, rows[-1].id)


def search(source, store, dir_id: bytes | None, crit: Criteria) -> dict:
    t0 = time.monotonic()
    now = int(time.time())
    where, params = crit.sql(now)
    results: list[dict] = []
    stopped = None
    with source.connect() as c:
        c.execute(text("SET SESSION max_statement_time = 10"))
        if dir_id is None:
            start = _root(c, store)          # Robinhood keeps no ENTRIES row for the root itself
        else:
            start = dir_id
            if c.execute(text("SELECT 1 FROM ENTRIES WHERE id = :d AND type = 'dir'"), {"d": start}).first() is None:
                raise HTTPException(404, "No such directory.")
        paths = {start: path_of(c, start)}
        frontier, visited = [start], 0
        # the filesystem root (VARS.RootId is stored as fsid/inode): indexed
        # search applies to the whole filesystem only
        root = _s(c.execute(text("SELECT value FROM VARS WHERE varname = 'RootId'")).scalar()).replace("/", ":")
        plan = _index_plan(c, crit) if root and _s(start) == root else None
        if plan is not None:
            results, stopped, examined = _indexed(c, start, paths, crit, plan, t0)
            frontier = []
        query = text(
            "SELECT n.id, n.parent_id, n.name, e.type, e.uid, e.gid, e.size, e.last_access, e.last_mod, "
            f"({where}) AS hit FROM NAMES n JOIN ENTRIES e ON e.id = n.id "
            f"WHERE n.parent_id IN :ids AND (e.type = 'dir' OR ({where}))"
        ).bindparams(bindparam("ids", expanding=True))
        while frontier and stopped is None:
            batch, frontier = frontier[:BATCH], frontier[BATCH:]
            visited += len(batch)
            for row in c.execute(query, {"ids": batch, **params}):
                path = paths[row.parent_id] + "/" + _s(row.name)
                if row.type == "dir":
                    paths[row.id] = path
                    frontier.append(row.id)
                if row.hit:
                    results.append({"path": path, "type": _s(row.type), "owner": _s(row.uid),
                                    "group": _s(row.gid), "size": int(row.size or 0),
                                    "last_access": row.last_access, "last_mod": row.last_mod})
            if len(results) >= MAX_RESULTS:
                stopped = "result limit"
            elif frontier and visited >= MAX_DIRS:
                stopped = "directory limit"
            elif frontier and time.monotonic() - t0 > TIME_BUDGET:
                stopped = "time limit"
    results.sort(key=lambda r: -r["size"])
    return {"dir": {"id": _s(start), "path": paths[start]}, "criteria": crit.as_dict(),
            "results": results[:MAX_RESULTS], "complete": stopped is None, "stopped": stopped,
            "method": "index" if plan else "walk", "index": plan[0] if plan else None,
            "entries_examined": examined if plan else None,
            "directories_searched": visited, "elapsed_seconds": round(time.monotonic() - t0, 2),
            "limits": {"directories": MAX_DIRS, "results": MAX_RESULTS, "seconds": TIME_BUDGET}}


def _params(type: Literal["file", "dir", "any"] = "file",
            owner: str | None = Query(None, max_length=127), group: str | None = Query(None, max_length=127),
            name: str | None = Query(None, max_length=255), min_size: int | None = Query(None, ge=0),
            max_size: int | None = Query(None, ge=0), not_accessed_days: int | None = Query(None, ge=0, le=36500),
            modified_within_days: int | None = Query(None, ge=0, le=36500)) -> Criteria:
    return Criteria(type=type, owner=owner or None, group=group or None, name=name or None,
                    min_size=min_size, max_size=max_size, not_accessed_days=not_accessed_days,
                    modified_within_days=modified_within_days)


@router.get("/search")
def search_api(request: Request, dir: str | None = Query(None, max_length=255), crit: Criteria = Depends(_params),
               p: Principal = Depends(require(Perm.PATHS_VIEW_ALL))) -> dict:
    out = search(request.app.state.engine, request.app.state.store_engine,
                 dir.encode() if dir else None, crit)
    audit("search", user=p.user.uid, client=request.client.host if request.client else None,
          path=out["dir"]["path"], criteria=out["criteria"], matches=len(out["results"]), complete=out["complete"])
    return out


@router.get("/search.csv")
def search_csv(request: Request, dir: str | None = Query(None, max_length=255), crit: Criteria = Depends(_params),
               p: Principal = Depends(require(Perm.PATHS_VIEW_ALL))) -> StreamingResponse:
    if not p.grants.allows(Perm.COLD_EXPORT):
        raise HTTPException(403, "Exporting needs the cold.export permission.")
    out = search(request.app.state.engine, request.app.state.store_engine,
                 dir.encode() if dir else None, crit)
    audit("search.export", user=p.user.uid, client=request.client.host if request.client else None,
          path=out["dir"]["path"], criteria=out["criteria"], matches=len(out["results"]), complete=out["complete"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["path", "type", "owner", "group", "size_bytes", "last_access", "last_mod"])
    for r in out["results"]:
        # a leading = + - @ would start a formula in a spreadsheet
        w.writerow([("'" + v) if isinstance(v, str) and v[:1] in "=+-@" else v for v in
                    (r["path"], r["type"], r["owner"], r["group"], r["size"], r["last_access"], r["last_mod"])])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="lamplighter-search.csv"'})
