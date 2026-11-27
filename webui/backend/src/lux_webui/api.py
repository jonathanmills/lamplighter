# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""JSON API (v1)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from .acl import Perm
from .audit import audit
from .auth import Principal, current_principal, require
from .db import robinhood_status, usage_summary
from .ldapdir import DirectoryError
from .machine import MachineClient, monitoring_client
from .store import age_bands, cold_owners, collector_runs

NO_RUN = "The collector has not completed a run yet."

router = APIRouter(prefix="/api/v1")


@router.get("/me")
def me(p: Principal = Depends(current_principal)) -> dict:
    u = p.user
    return {
        "uid": u.uid, "name": u.name, "mail": u.mail,
        "uid_number": u.uid_number, "gid_number": u.gid_number,
        "groups": list(u.groups),
        "roles": sorted(p.grants.roles),
        "permissions": sorted(x.value for x in p.grants.permissions),
        "login_method": p.session.method,
    }


@router.get("/status")
def status(request: Request, p: Principal = Depends(require(Perm.DASHBOARD_VIEW))) -> dict:
    return robinhood_status(request.app.state.engine)


@router.get("/usage/summary")
def usage(request: Request, p: Principal = Depends(require(Perm.USAGE_VIEW_AGGREGATES))) -> dict:
    return usage_summary(request.app.state.engine)


@router.get("/usage/age")
def usage_age(request: Request, basis: Literal["access", "modify"] = "access",
              p: Principal = Depends(require(Perm.USAGE_VIEW_AGGREGATES))) -> dict:
    """Files and bytes by time since last access (or modification)."""
    body = age_bands(request.app.state.store_engine, basis)
    if body is None:
        raise HTTPException(404, NO_RUN)
    return body


@router.get("/cold/owners")
def cold(request: Request, older_than: Literal["90d", "1y", "3y"] = "1y",
         limit: int = Query(25, ge=1, le=200),
         p: Principal = Depends(require(Perm.COLD_VIEW))) -> dict:
    """Owners holding the most data not accessed for ``older_than``, with
    their directory name and e-mail so data managers can contact them."""
    min_band = {"90d": 3, "1y": 4, "3y": 5}[older_than]
    body = cold_owners(request.app.state.store_engine, min_band, limit)
    if body is None:
        raise HTTPException(404, NO_RUN)
    directory = request.app.state.directory
    for o in body["owners"]:
        try:
            u = directory.find_user(o["user"])
        except DirectoryError:
            u = None
        o["name"] = u.name if u else None
        o["mail"] = u.mail if u else None
    audit("cold.owners", user=p.user.uid, client=request.client.host if request.client else None, older_than=older_than, count=len(body["owners"]))
    return body


@router.get("/cold/owners.csv")
def cold_csv(request: Request, older_than: Literal["90d", "1y", "3y"] = "1y",
             p: Principal = Depends(require(Perm.COLD_EXPORT))):
    """The cold-owner list as CSV, for owner contact or offload tooling."""
    import csv
    import io
    from fastapi.responses import Response
    body = cold(request, older_than, 200, p)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user", "name", "mail", f"bytes_not_accessed_{older_than}",
                f"files_not_accessed_{older_than}", "total_bytes"])
    for o in body["owners"]:
        w.writerow([o["user"], o["name"] or "", o["mail"] or "", o["cold_bytes"],
                    o["cold_files"], o["total_bytes"]])
    audit("cold.export", user=p.user.uid, client=request.client.host if request.client else None, older_than=older_than, rows=len(body["owners"]))
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="cold-owners-{older_than}.csv"'})


@router.get("/collector/runs")
def runs(request: Request, p: Principal = Depends(require(Perm.COLLECTOR_STATUS_VIEW))) -> list[dict]:
    return collector_runs(request.app.state.store_engine, 20)


@router.get("/monitoring")
def monitoring(request: Request, client: MachineClient = Depends(monitoring_client)) -> dict:
    """Facts and metrics for Checkmk. No states, no thresholds: Checkmk judges."""
    engine = request.app.state.engine
    try:
        body = {"lamplighter": robinhood_status(engine), "usage": usage_summary(engine, top=10)}
        body["database"] = {"reachable": True}
    except SQLAlchemyError as exc:
        body = {"database": {"reachable": False, "error": type(exc).__name__}}
    try:
        store = request.app.state.store_engine
        last = collector_runs(store, 1)
        ok = next((r for r in collector_runs(store, 20) if r["status"] == "ok"), None)
        body["collector"] = {
            "last_run": last[0] if last else None,
            "last_ok_finished": ok["finished"] if ok else None,
            "age_by_access": age_bands(store, "access"),
        }
    except SQLAlchemyError as exc:
        body["collector"] = {"error": type(exc).__name__}
    # Successful polls are not audited: Checkmk polls every minute and would
    # bury the events that matter. Refused requests are audited in machine.py.
    return body
