# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""JSON API (v1)."""

from __future__ import annotations

import time

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from .acl import Perm
from .audit import audit
from .auth import Principal, current_principal, require
from .export import Format, export, require_export
from .db import robinhood_status, usage_summary
from .ldapdir import DirectoryError
from .machine import MachineClient, monitoring_client
from .store import age_bands, cold_owners, collector_runs, policy_counts

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
         format: Format | None = Query(None, description="csv or json: download the table"),
         p: Principal = Depends(require(Perm.COLD_VIEW))) -> dict:
    """Owners holding the most data not accessed for ``older_than``, with
    their directory name and e-mail so data managers can contact them."""
    if format:
        require_export(p, "owner contact details")
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
    if format:
        # the columns the cold-owner CSV has always had
        rows = [{"user": o["user"], "name": o["name"], "mail": o["mail"],
                 f"bytes_not_accessed_{older_than}": o["cold_bytes"],
                 f"files_not_accessed_{older_than}": o["cold_files"], "total_bytes": o["total_bytes"]}
                for o in body["owners"]]
        return export(request, p, f"cold-owners-{older_than}", rows, format, older_than=older_than)
    audit("cold.owners", user=p.user.uid, client=request.client.host if request.client else None, older_than=older_than, count=len(body["owners"]))
    return body


@router.get("/cold/owners.csv")
def cold_csv(request: Request, older_than: Literal["90d", "1y", "3y"] = "1y",
             p: Principal = Depends(require(Perm.COLD_EXPORT))):
    """Kept for existing links: the same as /cold/owners?format=csv&limit=200."""
    return cold(request, older_than, 200, "csv", p)


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
    try:
        # Entries per fileclass and policy status (collector), e.g. how many
        # entries a classifier alert policy marked "alert", per fileclass.
        body["policies"] = policy_counts(request.app.state.store_engine)
    except SQLAlchemyError as exc:
        body["policies"] = {"error": type(exc).__name__}
    body["jobstats"] = _jobstats_rates(request)
    # Successful polls are not audited: Checkmk polls every minute and would
    # bury the events that matter. Refused requests are audited in machine.py.
    return body


def _jobstats_rates(request: Request) -> dict:
    """Filesystem-wide jobstats rates over the last 5 minutes, for Checkmk."""
    cfg = request.app.state.settings.jobstats
    if cfg is None:
        return {"configured": False}
    from .jobstats import source_status, triage_jobs
    now = int(time.time())
    window = 300
    try:
        js = triage_jobs(request.app.state.store_engine, window, "md", 1, now)
        sources = source_status(request.app.state.store_engine, cfg.stale_after_seconds, now)
    except SQLAlchemyError as exc:
        return {"configured": True, "error": type(exc).__name__}
    t = js["totals"]
    top = js["jobs"][0] if js["jobs"] else None
    return {"configured": True, "window_seconds": window, "as_of": js["as_of"],
            "md_ops_per_second": t["md_ops"] / window,
            "read_bytes_per_second": t["read_bytes"] / window,
            "write_bytes_per_second": t["write_bytes"] / window,
            "sources": len(sources), "sources_not_reporting": sum(1 for s in sources if s["stale"]),
            "top_md_job": {"job_id": top["job_id"], "md_ops_per_second": top["md_ops"] / window} if top else None}
