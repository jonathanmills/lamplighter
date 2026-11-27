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
"""

from __future__ import annotations

import csv
import io
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
