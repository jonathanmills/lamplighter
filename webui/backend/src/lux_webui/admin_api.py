# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Directory administration API (admin.ldap): configuration, connection
test, user lookup and role-mapping check.

The configuration view is built field by field from an explicit list, so
a secret can never appear in it, even if new settings are added later.
Settings are read-only here; they live in the config file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .acl import Perm
from .audit import audit
from .auth import Principal, require
from .ldapdiag import check_groups, test_servers
from .ldapdir import DirectoryError, norm_dn

router = APIRouter(prefix="/api/v1/admin/directory")
admin = require(Perm.ADMIN_LDAP)


@router.get("/config")
def config(request: Request, p: Principal = Depends(admin)) -> dict:
    s = request.app.state.settings
    l = s.ldap
    return {
        "ldap": {
            "servers": l.servers, "starttls": l.starttls, "ca_file": l.ca_file,
            "allow_insecure": l.allow_insecure, "bind_dn": l.bind_dn,
            "bind_password_file": l.bind_password_file,       # the path only, never the password
            "directory_type": l.directory_type.value, "timeout_seconds": l.timeout_seconds,
            "cache_ttl_seconds": l.cache_ttl_seconds,
            "users": {"base_dn": l.users.base_dn, "scope": l.users.scope, "filter": l.users.filter,
                      "login_attr": l.users.login_attr, "name_attr": l.users.name_attr,
                      "mail_attr": l.users.mail_attr, "uidnumber_attr": l.users.uidnumber_attr,
                      "gidnumber_attr": l.users.gidnumber_attr},
            "groups": {"base_dn": l.groups.base_dn, "scope": l.groups.scope, "filter": l.groups.filter,
                       "membership": l.groups.membership, "nested": l.groups.nested},
        },
        "login": {
            "oidc": None if not (s.oidc and s.oidc.enabled) else {
                "issuer": s.oidc.issuer, "client_id": s.oidc.client_id,
                "username_claim": s.oidc.username_claim},
            "ldap_bind": s.login.ldap_bind_enabled,
            "session_hours": round(s.server.session_ttl_seconds / 3600, 1),
        },
    }


@router.post("/test")
async def test(request: Request, p: Principal = Depends(admin)) -> dict:
    results = await run_in_threadpool(test_servers, request.app.state.directory)
    ok = all(r["ok"] for r in results)
    audit("admin.directory.test", user=p.user.uid, client=request.client.host if request.client else None, outcome="ok" if ok else "failed")
    return {"ok": ok, "servers": results}


@router.get("/lookup")
def lookup(request: Request, user: str = Query(..., min_length=1, max_length=256),
           p: Principal = Depends(admin)) -> dict:
    directory = request.app.state.directory
    policy = request.app.state.policy
    try:
        u = directory.find_user(user, refresh=True)
    except DirectoryError as exc:
        raise HTTPException(503, f"The directory is unavailable: {exc}")
    audit("admin.directory.lookup", user=p.user.uid, client=request.client.host if request.client else None, looked_up=user, found=u is not None)
    if u is None:
        raise HTTPException(404, f"No entry matches {user!r} with the configured user filter.")
    grants = policy.grants_for(u.groups)
    return {
        "uid": u.uid, "dn": u.dn, "name": u.name, "mail": u.mail,
        "uid_number": u.uid_number, "gid_number": u.gid_number,
        "groups": policy.explain(u.groups),
        "roles": sorted(grants.roles),
        "permissions": sorted(x.value for x in grants.permissions),
        "can_sign_in": grants.has_access,
    }


@router.get("/mappings")
def mappings(request: Request, p: Principal = Depends(admin)) -> dict:
    s = request.app.state.settings
    rows = [{"group": g, "roles": ["admin"], "via": "bootstrap admin group"}
            for g in s.access.bootstrap_admin_groups]
    rows += [{"group": m.group, "roles": sorted(m.roles), "via": "role mapping"}
             for m in s.access.role_mappings]
    try:
        status = check_groups(request.app.state.directory, [r["group"] for r in rows])
    except DirectoryError as exc:
        raise HTTPException(503, f"The directory is unavailable: {exc}")
    for r in rows:
        r.update(status.get(norm_dn(r["group"]), {"exists": False, "members": 0}))
    return {"mappings": rows}


# --- audit log (admin.audit.view) -------------------------------------------

audit_router = APIRouter(prefix="/api/v1/admin/audit")


@audit_router.get("")
def audit_events(request: Request, user: str | None = None, event: str | None = None,
                 outcome: str | None = None, since_hours: int = Query(24 * 7, ge=1, le=24 * 400),
                 before_id: int | None = None, limit: int = Query(200, ge=1, le=1000),
                 p: Principal = Depends(require(Perm.ADMIN_AUDIT_VIEW))) -> dict:
    """Newest first; page with before_id (the last id of the previous page)."""
    import json as _json
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text
    where = ["ts >= :since"]
    params: dict = {"since": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=since_hours),
                    "n": limit + 1}
    for col, val in (("user", user), ("event", event), ("outcome", outcome)):
        if val:
            where.append(f"{col} = :{col}")
            params[col] = val
    if before_id:
        where.append("id < :before")
        params["before"] = before_id
    with request.app.state.store_engine.connect() as c:
        rows = c.execute(text(f"SELECT id, ts, event, outcome, user, client, details FROM audit_log "
                              f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT :n"), params).all()
        events = [e for (e,) in c.execute(text("SELECT DISTINCT event FROM audit_log ORDER BY event"))]
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "events": events, "more": more,
        "items": [{"id": r[0], "at": r[1].replace(tzinfo=timezone.utc).timestamp(), "event": r[2],
                   "outcome": r[3], "user": r[4], "client": r[5],
                   "details": _json.loads(r[6]) if r[6] else {}} for r in rows],
    }


# --- access control (admin.acl), read-only ----------------------------------

access_router = APIRouter(prefix="/api/v1/admin/access")

PERM_TEXT = {
    "dashboard.view": "See the overview dashboard",
    "usage.view_aggregates": "See group usage and their own usage",
    "usage.view_all_users": "See every user's usage",
    "paths.view_own": "Browse their own files",
    "paths.view_all": "Browse everyone's files",
    "cold.view": "See cold data and the owners to contact",
    "cold.export": "Export cold-data owner lists (CSV)",
    "triage.view": "See slowdown triage (jobs, I/O)",
    "collector.status.view": "See collector runs",
    "admin.ldap": "Directory settings, connection test, user lookup",
    "admin.acl": "See roles, permissions and who holds them",
    "admin.audit.view": "Read the audit log",
}


@access_router.get("")
def access(request: Request, p: Principal = Depends(require(Perm.ADMIN_ACL))) -> dict:
    policy = request.app.state.policy
    s = request.app.state.settings
    directory = request.app.state.directory
    groups = ([(g, ["admin"], "bootstrap admin group") for g in s.access.bootstrap_admin_groups]
              + [(m.group, sorted(m.roles), "role mapping") for m in s.access.role_mappings])
    try:
        members = directory.group_members([g for g, _, _ in groups])
    except DirectoryError as exc:
        raise HTTPException(503, f"The directory is unavailable: {exc}")
    return {
        "permissions": [{"name": perm.value, "text": PERM_TEXT.get(perm.value, perm.value)} for perm in Perm],
        "roles": [{"name": r, "permissions": sorted(x.value for x in perms)}
                  for r, perms in sorted(policy.roles.items(), key=lambda kv: -len(kv[1]))],
        "groups": [{"group": g, "roles": roles, "via": via,
                    "members": members.get(norm_dn(g))} for g, roles, via in groups],
    }
