# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Fileclasses & policies API: entries and volume per Robinhood fileclass
and policy status, from the newest collector run (aggregates only)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .acl import Perm
from .auth import Principal, require
from .db import usage_summary
from .export import Format, export
from .store import policy_counts

router = APIRouter(prefix="/api/v1/policies")


@router.get("")
def policies(request: Request, format: Format | None = Query(None, description="csv or json: download the table"),
             table: Literal["fileclasses", "policies"] = "fileclasses",
             p: Principal = Depends(require(Perm.USAGE_VIEW_AGGREGATES))) -> dict:
    data = policy_counts(request.app.state.store_engine)
    if data is None:
        raise HTTPException(404, "no collector run yet")
    by_type = usage_summary(request.app.state.engine, top=1)["by_type"]
    data["filesystem"] = {"entries": sum(t["entries"] for t in by_type.values()),
                          "bytes": by_type.get("file", {}).get("bytes", 0)}
    if format:
        if table == "fileclasses":
            rows = [{"fileclass": fc, "entries": v["entries"], "bytes": v["bytes"], "statuses": v["statuses"]}
                    for fc, v in sorted(data["fileclasses"].items())]
        else:
            rows = [{"policy": pol, "status_field": v["field"], "status": st, **figs}
                    for pol, v in sorted(data["policies"].items()) for st, figs in sorted(v["statuses"].items())]
        return export(request, p, table, rows, format, run_id=data["run_id"])
    return data
