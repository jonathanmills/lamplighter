# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Triage API: "the filesystem is slow, who is doing what?"

Two views side by side (doc/webui-design.md section 5.4):
- Lustre jobstats, when configured: the busiest jobs, targets and a
  time series, from lamplighter-jobstats' tables (cheap, indexed by time).
  Job IDs are shown as Lustre labels them; with the default jobid_name
  ("%e.%u") the UID is resolved to a person through LDAP.
- Robinhood: recently modified data by owner (collector) and the
  changelog backlog (VARS).

No job scheduler is involved. Scheduler details come only from an
opt-in plug-in (config: jobstats.job_details), "none" by default.
"""

from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter, Depends, Query, Request

from .acl import Perm
from .auth import Principal, require
from .db import robinhood_status
from .jobstats import job_details_provider, source_status, triage_jobs
from .ldapdir import DirectoryError
from .store import recent_changes

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/triage")

WINDOWS = {"15m": 900, "1h": 3600, "6h": 6 * 3600, "24h": 86400}


def _people_by_number(request: Request) -> dict[int, object]:
    try:
        people = request.app.state.directory.all_people()
    except DirectoryError as exc:
        log.warning("names unavailable: %s", exc)
        return {}
    return {p.uid_number: p for p in people.values() if p.uid_number is not None}


@router.get("")
def triage(request: Request,
           window: str = Query("1h", pattern="^(15m|1h|6h|24h)$"),
           sort: str = Query("md", pattern="^(md|io)$"),
           p: Principal = Depends(require(Perm.TRIAGE_VIEW))) -> dict:
    settings = request.app.state.settings
    now = int(time.time())
    out: dict = {"window": window}

    cfg = settings.jobstats
    if cfg is None:
        out["jobstats"] = {"configured": False}
    else:
        js = triage_jobs(request.app.state.store_engine, WINDOWS[window], sort, 25, now)
        pattern = re.compile(cfg.jobid_pattern)
        people = _people_by_number(request)
        details = job_details_provider(cfg.job_details).details([j["job_id"] for j in js["jobs"]])
        for j in js["jobs"]:
            m = pattern.match(j["job_id"])
            parts = {k: v for k, v in (m.groupdict() if m else {}).items() if v is not None}
            j["parts"] = parts
            person = people.get(int(parts["uid"])) if parts.get("uid", "").isdigit() else None
            j["user"] = person.uid if person else None
            j["name"] = person.name if person else None
            j["details"] = details.get(j["job_id"], {})
        out["jobstats"] = {"configured": True, "source": cfg.source, "job_details": cfg.job_details,
                           "sources": source_status(request.app.state.store_engine,
                                                    cfg.stale_after_seconds, now), **js}

    status = robinhood_status(request.app.state.engine)
    recent = recent_changes(request.app.state.store_engine)
    if recent:
        try:
            names = request.app.state.directory.all_people()
        except DirectoryError:
            names = {}
        for o in recent["owners"]:
            person = names.get(o["user"])
            o["name"] = person.name if person else None
    out["robinhood"] = {"changelog": status.get("changelog"), "last_scan": status.get("last_scan"),
                        "recent": recent}
    return out
