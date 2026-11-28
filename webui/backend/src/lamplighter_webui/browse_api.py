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
from sqlalchemy import bindparam, text

from .acl import Perm
from .audit import audit
from .auth import Principal, require
from .export import Format, export, require_export

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


_OWN_COLS = ("r.dir_id, r.parent_dir, r.depth, r.path, r.owner AS dir_owner, o.files, o.bytes, "
             "o.newest_access, o.oldest_access, o.cold_1y_bytes, o.cold_3y_bytes")
_OWN_FROM = ("FROM dir_rollup r JOIN dir_rollup_owner o ON o.run_id = r.run_id AND o.dir_id = r.dir_id "
             "AND o.owner = :u WHERE r.run_id = :r")


def _own_row(r, uid: str, subdirs: int) -> dict:
    """A rolled-up directory as its owner-scoped view: the person's own
    figures, and no other person's name."""
    return {"id": _s(r.dir_id), "name": r.path.rsplit("/", 1)[-1] or r.path, "path": r.path,
            "depth": r.depth, "owner": r.dir_owner if r.dir_owner == uid else None, "top_owner": None,
            "top_owner_bytes": 0, "subdirs": subdirs, "files": int(r.files), "bytes": int(r.bytes),
            "newest_access": r.newest_access, "oldest_access": r.oldest_access,
            "cold_1y_bytes": int(r.cold_1y_bytes), "cold_3y_bytes": int(r.cold_3y_bytes)}


@router.get("/browse")
def browse(request: Request, dir: str | None = Query(None, max_length=255),
           format: Format | None = Query(None, description="csv or json: download the table"),
           table: Literal["subdirs", "files"] = "subdirs",
           p: Principal = Depends(require(Perm.PATHS_VIEW_ALL, Perm.PATHS_VIEW_OWN))) -> dict:
    """A directory: subtree totals, sub-directories and the largest files.

    With paths.view_all, everything. With only paths.view_own, the
    person's own data: directories that hold some of it (from the
    collector's per-owner rollup), their own totals, their own files, and
    no other person's name; any other directory is "No such directory".
    """
    store, source = request.app.state.store_engine, request.app.state.engine
    run_id, as_of = _latest_run(store)
    depth_limit = request.app.state.settings.collector.rollup_depth
    own = not p.grants.allows(Perm.PATHS_VIEW_ALL)
    uid = p.user.uid
    q = {"r": run_id, "u": uid}

    with store.connect() as c:
        where = "AND r.depth = 0" if dir is None else "AND r.dir_id = :d"
        if dir is not None:
            q["d"] = dir.encode()
        if own:
            cur = c.execute(text(f"SELECT {_OWN_COLS} {_OWN_FROM} {where}"), q).first()
            if cur is None and dir is None:
                raise HTTPException(404, "You own no files on this filesystem (as of the last collector run).")
            if cur is None and c.execute(text("SELECT 1 FROM dir_rollup r WHERE r.run_id = :r " + where),
                                         q).first():
                raise HTTPException(404, "No such directory.")     # rolled up, but none of it is theirs
            children = [] if cur is None else c.execute(text(
                f"SELECT {_OWN_COLS} {_OWN_FROM} AND o.parent_dir = :p ORDER BY o.bytes DESC"),
                {**q, "p": cur.dir_id}).all()
            counts = dict(c.execute(text(
                "SELECT parent_dir, COUNT(*) FROM dir_rollup_owner WHERE run_id = :r AND owner = :u "
                "AND parent_dir IN :ids GROUP BY parent_dir").bindparams(bindparam("ids", expanding=True)),
                {**q, "ids": [x.dir_id for x in children] + ([cur.dir_id] if cur is not None else [])}).all()) \
                if cur is not None else {}
        else:
            cur = c.execute(text(f"SELECT {_ROLLUP_COLS} FROM dir_rollup r WHERE r.run_id = :r {where}"),
                            q).first()
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
            # Below the rollup depth, a person may look inside a directory only
            # if the rolled-up directory above it holds some of their data.
            if own and cur is None and sc.execute(text(
                    "SELECT 1 FROM dir_rollup_owner WHERE run_id = :r AND owner = :u AND dir_id = :d"),
                    {**q, "d": walk}).first() is None:
                raise HTTPException(404, "No such directory.")
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
        depth = crumbs[-1]["depth"] if crumbs else 0

        # Live: sub-directories (where the rollup does not list them) and files.
        mine = " AND e.uid = :u" if own else ""
        live_dirs = []
        if (depth >= depth_limit) if own else not children:
            # own view: sub-directories they own, or with files of theirs directly inside
            sel = (" AND (e.uid = :u OR EXISTS (SELECT 1 FROM NAMES n2 JOIN ENTRIES e2 ON e2.id = n2.id "
                   "WHERE n2.parent_id = n.id AND e2.uid = :u))") if own else ""
            live_dirs = [{"id": _s(i), "name": _s(n), "owner": _s(u) if not own or _s(u) == uid else None,
                          "newest_access": a, "live": True}
                         for i, n, u, a in c.execute(text(
                             "SELECT n.id, n.name, e.uid, e.last_access FROM NAMES n JOIN ENTRIES e ON e.id = n.id "
                             f"WHERE n.parent_id = :d AND e.type = 'dir'{sel} ORDER BY n.name LIMIT :l"),
                             {"d": dir_id, "l": DIR_LIMIT, "u": uid})]
        nfiles = c.execute(text("SELECT COUNT(*) FROM NAMES n JOIN ENTRIES e ON e.id = n.id "
                                f"WHERE n.parent_id = :d AND e.type = 'file'{mine}"), {"d": dir_id, "u": uid}).scalar()
        files = [{"name": _s(n), "owner": _s(u), "size": int(sz or 0), "last_access": a, "last_mod": m}
                 for n, u, sz, a, m in c.execute(text(
                     "SELECT n.name, e.uid, e.size, e.last_access, e.last_mod FROM NAMES n "
                     f"JOIN ENTRIES e ON e.id = n.id WHERE n.parent_id = :d AND e.type = 'file'{mine} "
                     "ORDER BY e.size DESC LIMIT :l"), {"d": dir_id, "l": FILE_LIMIT, "u": uid})]

    if format and not own:
        require_export(p, "lists of paths")              # one's own files need nothing more
    if not format:
        audit("browse", user=p.user.uid, client=request.client.host if request.client else None, path=path,
              scope="own" if own else "all")
    if own:
        totals = _own_row(cur, uid, counts.get(cur.dir_id, 0)) if cur is not None else None
        subdirs = [_own_row(x, uid, counts.get(x.dir_id, 0)) for x in children] + live_dirs
    else:
        totals = _rollup_row(cur) if cur is not None else None
        subdirs = [_rollup_row(r) for r in children] + live_dirs
    if format:
        if table == "files":
            rows = [{"path": path.rstrip("/") + "/" + f["name"], **f} for f in files]
        else:
            rows = [{k: v for k, v in d.items() if k not in ("id", "live")} for d in subdirs]
        return export(request, p, f"browse-{table}", rows, format, path=path, scope="own" if own else "all")
    return {
        "as_of": as_of, "depth_limit": depth_limit, "scope": "own" if own else "all",
        "dir": {"id": _s(dir_id), "path": path, "depth": depth, "totals": totals},
        "ancestors": crumbs[:-1] if crumbs and crumbs[-1]["id"] == _s(dir_id) else crumbs,
        "subdirs": subdirs,
        "files": {"count": int(nfiles or 0), "shown": len(files), "items": files},
    }


@router.get("/cold/directories")
def coldest_directories(request: Request, older_than: Literal["1y", "3y"] = "1y",
                        limit: int = Query(25, ge=1, le=200),
                        format: Format | None = Query(None, description="csv or json: download the table"),
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
    if format:
        require_export(p, "lists of paths")
        return export(request, p, f"cold-directories-{older_than}",
                      [{k: v for k, v in _rollup_row(r).items() if k != "id"} for r in rows], format,
                      older_than=older_than)
    audit("cold.directories", user=p.user.uid, client=request.client.host if request.client else None,
          older_than=older_than, count=len(rows))
    return {"as_of": as_of, "older_than": older_than, "directories": [_rollup_row(r) for r in rows]}
