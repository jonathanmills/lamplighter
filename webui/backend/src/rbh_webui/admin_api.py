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
    audit("admin.directory.test", user=p.user.uid, outcome="ok" if ok else "failed")
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
    audit("admin.directory.lookup", user=p.user.uid, looked_up=user, found=u is not None)
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
