# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Fileclasses & policies API: entries and volume per Robinhood fileclass
and policy status, from the newest collector run (aggregates only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .acl import Perm
from .auth import Principal, require
from .db import usage_summary
from .store import policy_counts

router = APIRouter(prefix="/api/v1/policies")


@router.get("")
def policies(request: Request, p: Principal = Depends(require(Perm.USAGE_VIEW_AGGREGATES))) -> dict:
    data = policy_counts(request.app.state.store_engine)
    if data is None:
        raise HTTPException(404, "no collector run yet")
    by_type = usage_summary(request.app.state.engine, top=1)["by_type"]
    data["filesystem"] = {"entries": sum(t["entries"] for t in by_type.values()),
                          "bytes": by_type.get("file", {}).get("bytes", 0)}
    return data
