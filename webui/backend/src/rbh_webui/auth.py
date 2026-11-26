# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""User login: OIDC (Keycloak) and optional direct LDAP bind.

Whichever way a person logs in, their identity and groups come from
LDAP, and their permissions from the ACL policy. A person whose groups
grant no role is refused.
"""

from __future__ import annotations

import html
import logging
import ssl
from dataclasses import dataclass
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from .acl import AccessPolicy, Grants, Perm
from .audit import audit
from .config import Settings, read_secret
from .ldapdir import Directory, DirectoryError, DirUser
from .sessions import Session, SessionStore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")


@dataclass(frozen=True)
class Principal:
    user: DirUser
    grants: Grants
    session: Session


def setup_oauth(settings: Settings) -> OAuth | None:
    o = settings.oidc
    if not (o and o.enabled):
        return None
    oauth = OAuth()
    client_kwargs = {"scope": o.scope, "code_challenge_method": "S256"}
    if o.ca_file:
        client_kwargs["verify"] = ssl.create_default_context(cafile=o.ca_file)
    oauth.register(
        name="idp",
        client_id=o.client_id,
        client_secret=read_secret(o.client_secret_file),
        server_metadata_url=o.issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_kwargs=client_kwargs,
    )
    return oauth


def _client(request: Request) -> str | None:
    return request.client.host if request.client else None


# --- pages ------------------------------------------------------------

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robinhood · Sign in</title>
<style>
:root{{color-scheme:dark}}
*,*::before,*::after{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;
 font:15px/1.5 Ubuntu,"Segoe UI",system-ui,sans-serif;color:#DFDBD2;
 background:linear-gradient(135deg,#77216F 0%,#5E2750 35%,#2C001E 100%);padding:16px}}
main{{background:#262523;border:1px solid #4D4C47;border-radius:12px;padding:28px;
 width:min(380px,100%);display:grid;gap:16px}}
h1{{margin:0;font-size:1.3rem;font-weight:500}} p{{margin:0;color:#AEA79F}}
.btn{{display:block;text-align:center;background:#4B9CD3;color:#13294B;border:0;border-radius:6px;
 padding:10px;font-family:inherit;font-weight:500;font-size:.95rem;text-decoration:none;cursor:pointer;width:100%}}
form{{display:grid;gap:10px}} label{{display:grid;gap:4px;font-size:.85rem;color:#AEA79F}}
input{{background:#2F2E2B;color:#DFDBD2;border:1px solid #4D4C47;border-radius:6px;padding:8px;font:inherit}}
input:focus,.btn:focus{{outline:2px solid #4B9CD3;outline-offset:2px}}
.err{{color:#FF5F57}} hr{{border:0;border-top:1px solid #4D4C47;width:100%}}
</style></head><body><main>
<h1>Robinhood</h1>{body}</main></body></html>"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(body=body), status_code=status)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    s: Settings = request.app.state.settings
    parts = ["<p>Sign in to see filesystem usage and data management views.</p>"]
    if error:
        parts.append(f'<p class="err">{html.escape(error)}</p>')
    if s.oidc and s.oidc.enabled:
        parts.append('<a class="btn" href="/auth/oidc">Sign in with single sign-on</a>')
    if s.login.ldap_bind_enabled:
        if s.oidc and s.oidc.enabled:
            parts.append("<hr>")
        parts.append(
            '<form method="post" action="/auth/ldap">'
            '<label for="username">Username<input id="username" name="username" autocomplete="username" required></label>'
            '<label for="password">Password<input id="password" name="password" type="password" autocomplete="current-password" required></label>'
            '<button class="btn" type="submit">Sign in with directory password</button></form>')
    return _page("".join(parts))


def _start_session(request: Request, user: DirUser, method: str,
                   id_token: str | None = None) -> RedirectResponse:
    policy: AccessPolicy = request.app.state.policy
    grants = policy.grants_for(user.groups)
    if not grants.has_access:
        audit("login", user=user.uid, outcome="denied", reason="no role", method=method,
              client=_client(request))
        raise HTTPException(403, "Your account has no access to Robinhood. "
                                 "Ask an administrator to add you to a Robinhood group.")
    store: SessionStore = request.app.state.sessions
    session = store.create(user.uid, method, id_token=id_token)
    request.session.clear()
    request.session["sid"] = session.sid
    audit("login", user=user.uid, method=method, roles=sorted(grants.roles),
          client=_client(request))
    return RedirectResponse("/", status_code=303)


# --- OIDC ---------------------------------------------------------------

@router.get("/oidc")
async def oidc_start(request: Request):
    oauth: OAuth | None = request.app.state.oauth
    if oauth is None:
        raise HTTPException(404)
    base = request.app.state.settings.server.base_url.rstrip("/")
    return await oauth.idp.authorize_redirect(request, base + "/auth/callback")


@router.get("/callback")
async def oidc_callback(request: Request):
    oauth: OAuth | None = request.app.state.oauth
    s: Settings = request.app.state.settings
    if oauth is None:
        raise HTTPException(404)
    try:
        token = await oauth.idp.authorize_access_token(request)
    except OAuthError as exc:
        audit("login", outcome="error", method="oidc", reason=exc.error, client=_client(request))
        return RedirectResponse("/auth/login?" + urlencode({"error": "Sign-in failed. Please try again."}), 303)
    claims = token.get("userinfo") or {}
    username = claims.get(s.oidc.username_claim)
    if not username:
        audit("login", outcome="error", method="oidc", reason="no username claim")
        raise HTTPException(403, f"The identity provider sent no {s.oidc.username_claim!r} claim.")
    directory: Directory = request.app.state.directory
    try:
        user = await run_in_threadpool(directory.find_user, username, refresh=True)
    except DirectoryError as exc:
        log.error("ldap lookup failed: %s", exc)
        raise HTTPException(503, "The directory is unavailable. Please try again shortly.")
    if user is None:
        audit("login", user=username, outcome="denied", reason="not in directory", method="oidc")
        raise HTTPException(403, "Your account was not found in the directory.")
    return _start_session(request, user, "oidc", id_token=token.get("id_token"))


# --- direct LDAP bind ---------------------------------------------------

@router.post("/ldap")
def ldap_login(request: Request, username: str = Form(...), password: str = Form(...)):
    s: Settings = request.app.state.settings
    if not s.login.ldap_bind_enabled:
        raise HTTPException(404)
    directory: Directory = request.app.state.directory
    try:
        user = directory.authenticate(username, password)
    except DirectoryError as exc:
        log.error("ldap authentication failed: %s", exc)
        raise HTTPException(503, "The directory is unavailable. Please try again shortly.")
    if user is None:
        audit("login", user=username, outcome="denied", reason="bad credentials",
              method="ldap", client=_client(request))
        return RedirectResponse("/auth/login?" + urlencode({"error": "Wrong username or password."}), 303)
    return _start_session(request, user, "ldap")


# --- logout -------------------------------------------------------------

@router.post("/logout")
async def logout(request: Request):
    store: SessionStore = request.app.state.sessions
    session = store.delete(request.session.get("sid"))
    request.session.clear()
    if session is None:
        return RedirectResponse("/auth/login", 303)
    audit("logout", user=session.uid, method=session.method, client=_client(request))
    oauth: OAuth | None = request.app.state.oauth
    if session.method == "oidc" and oauth is not None:
        meta = await oauth.idp.load_server_metadata()
        end = meta.get("end_session_endpoint")
        if end:
            base = request.app.state.settings.server.base_url.rstrip("/")
            params = {"post_logout_redirect_uri": base + "/auth/login",
                      "client_id": request.app.state.settings.oidc.client_id}
            if session.id_token:
                params["id_token_hint"] = session.id_token
            return RedirectResponse(end + "?" + urlencode(params), 303)
    return RedirectResponse("/auth/login", 303)


# --- dependencies for API routes ----------------------------------------

def current_principal(request: Request) -> Principal:
    """The logged-in person (sync: runs in a worker thread for LDAP)."""
    store: SessionStore = request.app.state.sessions
    session = store.get(request.session.get("sid"))
    if session is None:
        raise HTTPException(401, "Not signed in.")
    directory: Directory = request.app.state.directory
    try:
        user = directory.find_user(session.uid)
    except DirectoryError:
        raise HTTPException(503, "The directory is unavailable.")
    if user is None:
        store.delete(session.sid)
        raise HTTPException(401, "Account no longer in the directory.")
    grants = request.app.state.policy.grants_for(user.groups)
    if not grants.has_access:
        raise HTTPException(403, "Your account no longer has access.")
    return Principal(user=user, grants=grants, session=session)


def require(perm: Perm):
    def dep(request: Request, p: Principal = Depends(current_principal)) -> Principal:
        if not p.grants.allows(perm):
            audit("denied", user=p.user.uid, outcome="denied", client=request.client.host if request.client else None, permission=perm.value, path=request.url.path)
            raise HTTPException(403, f"Missing permission: {perm.value}")
        return p
    return dep
