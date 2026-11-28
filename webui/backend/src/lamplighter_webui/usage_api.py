# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Users & groups API.

Live figures come from ACCT_STAT (cheap). History and age come from the
collector's snapshots. Names, e-mail and group descriptions come from
LDAP in one cached bulk search each.

Scope: without usage.view_all_users, a person sees only their own user
row and detail page; the Groups list is visible with usage.view_aggregates.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .acl import Perm
from .auth import Principal, require
from .db import usage_by, usage_summary, user_usage
from .export import Format, export
from .ldapdir import DirectoryError
from .store import snapshot_bytes, usage_history, user_age_bands, user_history

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/usage")


def sees_all_users(p: Principal) -> bool:
    return p.grants.allows(Perm.USAGE_VIEW_ALL_USERS)


def _people(request: Request) -> dict:
    try:
        return request.app.state.directory.all_people()
    except DirectoryError as exc:
        log.warning("names unavailable: %s", exc)
        return {}


def _filesystem_bytes(request: Request) -> int:
    return sum(t["bytes"] for t in usage_summary(request.app.state.engine, top=1)["by_type"].values())


@router.get("/users")
def users(request: Request, format: Format | None = Query(None, description="csv or json: download the table"),
          p: Principal = Depends(require(Perm.USAGE_VIEW_AGGREGATES))) -> dict:
    rows = usage_by(request.app.state.engine, "uid")
    everyone = sees_all_users(p)
    if not everyone:
        rows = [r for r in rows if r["key"] == p.user.uid]
    as_of, snap = snapshot_bytes(request.app.state.store_engine, "uid")
    people = _people(request)
    out = []
    for r in rows:
        user = r.pop("key")
        person = people.get(user)
        out.append({"user": user, "name": person.name if person else None,
                    "mail": person.mail if person else None, **r,
                    # live ACCT_STAT minus the newest collector snapshot
                    "change_bytes": (r["bytes"] - snap[user]) if user in snap else None})
    if format:
        # other people's e-mail addresses go out only with cold.export
        drop = {"mail"} if everyone and not p.grants.allows(Perm.COLD_EXPORT) else set()
        return export(request, p, "users", [{k: v for k, v in r.items() if k not in drop} for r in out], format)
    return {"scope": "all" if everyone else "self", "snapshot_as_of": as_of,
            "filesystem_bytes": _filesystem_bytes(request), "users": out}


@router.get("/groups")
def groups(request: Request, format: Format | None = Query(None, description="csv or json: download the table"),
           p: Principal = Depends(require(Perm.USAGE_VIEW_AGGREGATES))) -> dict:
    rows = usage_by(request.app.state.engine, "gid")
    as_of, snap = snapshot_bytes(request.app.state.store_engine, "gid")
    try:
        dir_groups = request.app.state.directory.all_unix_groups()
    except DirectoryError as exc:
        log.warning("group details unavailable: %s", exc)
        dir_groups = {}
    out = []
    for r in rows:
        g = dir_groups.get(r["key"])
        name = r.pop("key")
        out.append({"group": name, "description": g.description if g else None,
                    "members": g.members if g else None, **r,
                    "change_bytes": (r["bytes"] - snap[name]) if name in snap else None})
    if format:
        return export(request, p, "groups", out, format)
    return {"snapshot_as_of": as_of, "filesystem_bytes": _filesystem_bytes(request), "groups": out}


@router.get("/users/{uid}")
def user_detail(uid: str, request: Request,
                p: Principal = Depends(require(Perm.USAGE_VIEW_AGGREGATES))) -> dict:
    if uid != p.user.uid and not sees_all_users(p):
        raise HTTPException(403, "Missing permission: usage.view_all_users")
    body = user_usage(request.app.state.engine, uid)
    if body is None:
        raise HTTPException(404, f"No data owned by {uid!r}.")
    person = _people(request).get(uid)
    body.update({
        "user": uid, "name": person.name if person else None, "mail": person.mail if person else None,
        "uid_number": person.uid_number if person else None,
        "filesystem_bytes": _filesystem_bytes(request),
        "history": user_history(request.app.state.store_engine, uid),
        "age": user_age_bands(request.app.state.store_engine, uid),
    })
    return body


@router.get("/history")
def history(request: Request, by: Literal["users", "groups"] = "users", top: int = Query(8, ge=1, le=8),
            p: Principal = Depends(require(Perm.USAGE_VIEW_AGGREGATES))) -> dict:
    """Volume over the collector runs, the largest users or groups stacked
    (the old GUI's stacked graphs). Users need usage.view_all_users."""
    if by == "users" and not sees_all_users(p):
        raise HTTPException(403, "Missing permission: usage.view_all_users")
    return {"by": by, **usage_history(request.app.state.store_engine, "uid" if by == "users" else "gid", top)}

