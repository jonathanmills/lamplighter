# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Browse and coldest-directories APIs.

Subtree totals come from the collector's dir_rollup (down to
collector.rollup_depth). Below that depth, and for the files inside any
directory, Browse lists entries live through Robinhood's indexed
NAMES.parent_id: cheap, bounded, and with no subtree totals.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from .acl import Perm
from .audit import audit
from .auth import Principal, require

router = APIRouter(prefix="/api/v1")

FILE_LIMIT = 200
DIR_LIMIT = 500


def _s(v) -> str | None:
    if v is None:
        return None
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)


def _latest_run(store) -> tuple[int, int]:
    with store.connect() as c:
        row = c.execute(text("SELECT id, UNIX_TIMESTAMP(finished_at) FROM collector_runs "
                             "WHERE status = 'ok' ORDER BY id DESC LIMIT 1")).first()
    if not row:
        raise HTTPException(404, "The collector has not completed a run yet.")
    return int(row[0]), int(row[1])


def _rollup_row(r) -> dict:
    return {"id": _s(r.dir_id), "name": r.path.rsplit("/", 1)[-1] or r.path, "path": r.path,
            "depth": r.depth, "owner": r.owner, "top_owner": r.top_owner,
            "top_owner_bytes": int(r.top_owner_bytes), "subdirs": int(r.subdirs), "files": int(r.files),
            "bytes": int(r.bytes), "newest_access": r.newest_access, "oldest_access": r.oldest_access,
            "cold_1y_bytes": int(r.cold_1y_bytes), "cold_3y_bytes": int(r.cold_3y_bytes)}


_ROLLUP_COLS = ("dir_id, parent_dir, depth, path, owner, top_owner, top_owner_bytes, subdirs, files, "
                "bytes, newest_access, oldest_access, cold_1y_bytes, cold_3y_bytes")


@router.get("/browse")
def browse(request: Request, dir: str | None = Query(None, max_length=255),
           p: Principal = Depends(require(Perm.PATHS_VIEW_ALL))) -> dict:
    store, source = request.app.state.store_engine, request.app.state.engine
    run_id, as_of = _latest_run(store)
    depth_limit = request.app.state.settings.collector.rollup_depth

    with store.connect() as c:
        if dir is None:
            cur = c.execute(text(f"SELECT {_ROLLUP_COLS} FROM dir_rollup WHERE run_id = :r AND depth = 0"),
                            {"r": run_id}).first()
        else:
            cur = c.execute(text(f"SELECT {_ROLLUP_COLS} FROM dir_rollup WHERE run_id = :r AND dir_id = :d"),
                            {"r": run_id, "d": dir.encode()}).first()
        children = [] if cur is None else c.execute(text(
            f"SELECT {_ROLLUP_COLS} FROM dir_rollup WHERE run_id = :r AND parent_dir = :d ORDER BY bytes DESC"),
            {"r": run_id, "d": cur.dir_id}).all()
    if cur is None and dir is None:
        raise HTTPException(404, "No directory totals yet: run the collector.")
    dir_id = cur.dir_id if cur is not None else dir.encode()

    with source.connect() as c:
        c.execute(text("SET SESSION max_statement_time = 10"))
        # Who is this directory, and where is it? (live, for directories below the rollup depth)
        if cur is None:
            row = c.execute(text("SELECT n.parent_id, n.name, e.uid FROM NAMES n JOIN ENTRIES e ON e.id = n.id "
                                 "WHERE n.id = :d AND e.type = 'dir' LIMIT 1"), {"d": dir_id}).first()
            if row is None:
                raise HTTPException(404, "No such directory.")
        # Ancestors: walk up through the index until a directory with a known path.
        chain, walk = [], dir_id
        with store.connect() as sc:
            for _ in range(256):
                known = sc.execute(text("SELECT path, depth, parent_dir FROM dir_rollup "
                                        "WHERE run_id = :r AND dir_id = :d"), {"r": run_id, "d": walk}).first()
                if known is not None:
                    base_path, base_depth = known.path, known.depth
                    break
                r = c.execute(text("SELECT parent_id, name FROM NAMES WHERE id = :d LIMIT 1"), {"d": walk}).first()
                if r is None:
                    raise HTTPException(404, "No such directory.")
                chain.append((walk, _s(r.name)))
                walk = r.parent_id
            else:
                raise HTTPException(500, "Directory tree too deep.")
            ancestors_rows = sc.execute(text(
                "SELECT dir_id, path, depth FROM dir_rollup WHERE run_id = :r AND depth <= :dp"),
                {"r": run_id, "dp": base_depth}).all()
        # breadcrumb: known (rolled-up) ancestors by path prefix, then the live chain
        crumbs = sorted(({"id": _s(a.dir_id), "name": a.path.rsplit("/", 1)[-1] or a.path, "depth": a.depth}
                         for a in ancestors_rows if base_path == a.path or base_path.startswith(a.path.rstrip("/") + "/")),
                        key=lambda x: x["depth"])
        for i, (cid, name) in enumerate(reversed(chain)):
            crumbs.append({"id": _s(cid), "name": name, "depth": base_depth + i + 1})
        path = base_path.rstrip("/") + "".join("/" + n for _, n in reversed(chain)) if chain else base_path

        # Live: sub-directories (only when the rollup has none below this level) and files.
        live_dirs = []
        if not children:
            live_dirs = [{"id": _s(i), "name": _s(n), "owner": _s(u), "newest_access": a, "live": True}
                         for i, n, u, a in c.execute(text(
                             "SELECT n.id, n.name, e.uid, e.last_access FROM NAMES n JOIN ENTRIES e ON e.id = n.id "
                             "WHERE n.parent_id = :d AND e.type = 'dir' ORDER BY n.name LIMIT :l"),
                             {"d": dir_id, "l": DIR_LIMIT})]
        nfiles = c.execute(text("SELECT COUNT(*) FROM NAMES n JOIN ENTRIES e ON e.id = n.id "
                                "WHERE n.parent_id = :d AND e.type = 'file'"), {"d": dir_id}).scalar()
        files = [{"name": _s(n), "owner": _s(u), "size": int(sz or 0), "last_access": a, "last_mod": m}
                 for n, u, sz, a, m in c.execute(text(
                     "SELECT n.name, e.uid, e.size, e.last_access, e.last_mod FROM NAMES n "
                     "JOIN ENTRIES e ON e.id = n.id WHERE n.parent_id = :d AND e.type = 'file' "
                     "ORDER BY e.size DESC LIMIT :l"), {"d": dir_id, "l": FILE_LIMIT})]

    audit("browse", user=p.user.uid, client=request.client.host if request.client else None, path=path)
    return {
        "as_of": as_of, "depth_limit": depth_limit,
        "dir": {"id": _s(dir_id), "path": path, "depth": crumbs[-1]["depth"] if crumbs else 0,
                "totals": _rollup_row(cur) if cur is not None else None},
        "ancestors": crumbs[:-1] if crumbs and crumbs[-1]["id"] == _s(dir_id) else crumbs,
        "subdirs": [_rollup_row(r) for r in children] + live_dirs,
        "files": {"count": int(nfiles or 0), "shown": len(files), "items": files},
    }


@router.get("/cold/directories")
def coldest_directories(request: Request, older_than: Literal["1y", "3y"] = "1y",
                        limit: int = Query(25, ge=1, le=200),
                        p: Principal = Depends(require(Perm.COLD_VIEW))) -> dict:
    """The coldest *datasets*: rolled-up directories with no tracked sub-directories
    (leaves of the rollup tree), ranked by data not read for ``older_than``."""
    store = request.app.state.store_engine
    run_id, as_of = _latest_run(store)
    col = {"1y": "cold_1y_bytes", "3y": "cold_3y_bytes"}[older_than]
    with store.connect() as c:
        rows = c.execute(text(
            f"SELECT {_ROLLUP_COLS} FROM dir_rollup d WHERE run_id = :r AND {col} > 0 AND NOT EXISTS "
            f"(SELECT 1 FROM dir_rollup k WHERE k.run_id = d.run_id AND k.parent_dir = d.dir_id) "
            f"ORDER BY {col} DESC LIMIT :n"), {"r": run_id, "n": limit}).all()
    audit("cold.directories", user=p.user.uid, client=request.client.host if request.client else None,
          older_than=older_than, count=len(rows))
    return {"as_of": as_of, "older_than": older_than, "directories": [_rollup_row(r) for r in rows]}
