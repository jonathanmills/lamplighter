# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Scheduled reports API (reports.py).

Seeing a report, its runs and its graph needs the permissions of its
source; creating, editing, deleting and "Run now" also need
reports.manage. Downloads of a run go through export.py (audited).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError

from . import reports as R
from .acl import Perm
from .audit import audit
from .auth import Principal, current_principal, require
from .export import Format, export
from .ldapdir import DirectoryError

router = APIRouter(prefix="/api/v1/reports")


def _client(request: Request) -> str | None:
    return request.client.host if request.client else None


def _visible(request: Request, p: Principal, report_id: int) -> dict:
    report = R.get_report(request.app.state.store_engine, report_id)
    if report is None or not R.may_use(p.grants, R.SOURCES[report["source"]]):
        raise HTTPException(404, "No such report.")     # nor confirm that it exists
    return report


def _checked(request: Request, p: Principal, body: dict) -> dict:
    try:
        d = R.validate(request.app.state.settings, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not R.may_use(p.grants, R.SOURCES[d["source"]]):
        raise HTTPException(403, "You may not see this source's data, so you may not report on it.")
    unknown = []
    for uid in d["recipients"]:
        try:
            if request.app.state.directory.find_user(uid) is None:
                unknown.append(uid)
        except DirectoryError:
            raise HTTPException(503, "The directory is unavailable.")
    if unknown:
        raise HTTPException(422, f"Recipients not in the directory: {', '.join(unknown)}")
    return d


@router.get("/sources")
def sources(request: Request, p: Principal = Depends(current_principal)) -> dict:
    """The sources this person may report on, with their parameters (editor)."""
    return {"email": request.app.state.settings.reports.smtp is not None,
            "max_keep_runs": request.app.state.settings.reports.max_keep_runs,
            "may_manage": p.grants.allows(Perm.REPORTS_MANAGE),
            "sources": [{"key": s.key, "title": s.title, "params": [x.as_dict() for x in s.params]}
                        for s in R.SOURCES.values() if R.may_use(p.grants, s)]}


@router.get("")
def list_reports(request: Request, p: Principal = Depends(current_principal)) -> dict:
    rows = [r for r in R.list_reports(request.app.state.store_engine) if R.may_use(p.grants, R.SOURCES[r["source"]])]
    for r in rows:
        r["source_title"] = R.SOURCES[r["source"]].title
    return {"may_manage": p.grants.allows(Perm.REPORTS_MANAGE), "reports": rows}


@router.get("/{report_id}")
def report(report_id: int, request: Request, p: Principal = Depends(current_principal)) -> dict:
    r = _visible(request, p, report_id)
    store = request.app.state.store_engine
    r["source_title"] = R.SOURCES[r["source"]].title
    r["runs"] = R.runs(store, report_id)
    r["latest"] = R.run_data(store, report_id)
    r["series"] = R.series(store, r)
    audit("report.view", user=p.user.uid, client=_client(request), report=r["name"])
    return r


@router.get("/{report_id}/runs/{run_id}")
def run_table(report_id: int, run_id: int, request: Request,
              format: Format | None = Query(None, description="csv or json: download the table"),
              p: Principal = Depends(current_principal)) -> Any:
    r = _visible(request, p, report_id)
    data = R.run_data(request.app.state.store_engine, report_id, run_id)
    if data is None:
        raise HTTPException(404, "No such run, or it has no results.")
    if format:
        return export(request, p, f"report-{report_id}-run-{run_id}", data["rows"], format,
                      columns=data["columns"], report=r["name"])
    return data


@router.post("")
def create(request: Request, body: dict = Body(...),
           p: Principal = Depends(require(Perm.REPORTS_MANAGE))) -> dict:
    d = _checked(request, p, body)
    try:
        rid = R.save_report(request.app.state.store_engine, d, p.user.uid)
    except IntegrityError:
        raise HTTPException(409, f"A report named {d['name']!r} already exists.")
    audit("report.create", user=p.user.uid, client=_client(request), report=d["name"], source=d["source"],
          schedule=d["schedule"], recipients=d["recipients"])
    return {"id": rid}


@router.put("/{report_id}")
def update(report_id: int, request: Request, body: dict = Body(...),
           p: Principal = Depends(require(Perm.REPORTS_MANAGE))) -> dict:
    _visible(request, p, report_id)
    d = _checked(request, p, body)
    try:
        R.save_report(request.app.state.store_engine, d, p.user.uid, report_id)
    except IntegrityError:
        raise HTTPException(409, f"A report named {d['name']!r} already exists.")
    audit("report.update", user=p.user.uid, client=_client(request), report=d["name"], source=d["source"],
          schedule=d["schedule"], recipients=d["recipients"])
    return {"id": report_id}


@router.delete("/{report_id}")
def delete(report_id: int, request: Request, p: Principal = Depends(require(Perm.REPORTS_MANAGE))) -> dict:
    r = _visible(request, p, report_id)
    R.delete_report(request.app.state.store_engine, report_id)
    audit("report.delete", user=p.user.uid, client=_client(request), report=r["name"])
    return {"deleted": report_id}


@router.post("/{report_id}/run")
def run_now(report_id: int, request: Request, p: Principal = Depends(require(Perm.REPORTS_MANAGE))) -> dict:
    r = _visible(request, p, report_id)
    return R.run(request.app.state, r, p.user.uid)
