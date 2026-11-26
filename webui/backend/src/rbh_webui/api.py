# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""JSON API (v1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import SQLAlchemyError

from .acl import Perm
from .audit import audit
from .auth import Principal, current_principal, require
from .db import robinhood_status, usage_summary
from .machine import MachineClient, monitoring_client

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


@router.get("/usage/summary")
def usage(request: Request, p: Principal = Depends(require(Perm.USAGE_VIEW_AGGREGATES))) -> dict:
    return usage_summary(request.app.state.engine)


@router.get("/monitoring")
def monitoring(request: Request, client: MachineClient = Depends(monitoring_client)) -> dict:
    """Facts and metrics for Checkmk. No states, no thresholds: Checkmk judges."""
    engine = request.app.state.engine
    try:
        body = {"robinhood": robinhood_status(engine), "usage": usage_summary(engine, top=10)}
        body["database"] = {"reachable": True}
    except SQLAlchemyError as exc:
        body = {"database": {"reachable": False, "error": type(exc).__name__}}
    audit("monitoring", client_id=client.client_id)
    return body
