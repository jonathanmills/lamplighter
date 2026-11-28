# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Application factory: ``uvicorn lamplighter_webui.app:create_app --factory``."""

from __future__ import annotations

import html
import logging
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .acl import AccessPolicy
from .audit import attach_store
from .api import router as api_router
from .usage_api import router as usage_router
from .browse_api import router as browse_router
from .triage_api import router as triage_router
from .policies_api import router as policies_router
from .search_api import router as search_router
from .reports_api import router as reports_router
from .admin_api import access_router, audit_router, router as admin_router
from .auth import _page, current_principal, router as auth_router, setup_oauth
from .config import Settings, load_settings, read_secret
from .db import make_engine
from .ldapdir import Directory
from .machine import TokenVerifier
from .sessions import SessionStore
from .store import migrate

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "frame-ancestors 'none'; form-action 'self' https:; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    app = FastAPI(title="Lamplighter web UI", version=__version__,
                  docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json")
    app.state.settings = settings
    app.state.directory = Directory(settings.ldap)
    app.state.policy = AccessPolicy(settings.access)
    app.state.sessions = SessionStore(settings.server.session_ttl_seconds)
    app.state.oauth = setup_oauth(settings)
    app.state.engine = make_engine(settings.database)          # Robinhood (read only)
    app.state.store_engine = make_engine(settings.store)       # lamplighter_webui (collector output, audit)
    migrate(app.state.store_engine)
    attach_store(app.state.store_engine)
    app.state.token_verifier = (TokenVerifier(settings.oidc, settings.monitoring)
                                if settings.oidc and settings.monitoring and settings.monitoring.enabled
                                else None)

    base = urlsplit(settings.server.base_url)
    allowed_origin = f"{base.scheme}://{base.netloc}"

    @app.middleware("http")
    async def origin_check_and_headers(request: Request, call_next):
        # CSRF defence for cookie-authenticated writes: the browser's Origin
        # (or Referer) must be our own. Bearer-token API clients are exempt.
        if (request.method in UNSAFE_METHODS
                and not request.headers.get("authorization", "").lower().startswith("bearer ")):
            origin = request.headers.get("origin")
            if not origin and (ref := request.headers.get("referer")):
                r = urlsplit(ref)
                origin = f"{r.scheme}://{r.netloc}"
            if origin != allowed_origin:
                return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    # Outermost: the signed cookie holds only the session id and the
    # short-lived OIDC state/PKCE verifier.
    app.add_middleware(SessionMiddleware,
                       secret_key=read_secret(settings.server.session_secret_file),
                       session_cookie="lamplighter_session", https_only=True,
                       same_site=settings.server.cookie_samesite,
                       max_age=settings.server.session_ttl_seconds)

    app.include_router(auth_router)
    app.include_router(api_router)
    app.include_router(usage_router)
    app.include_router(browse_router)
    app.include_router(triage_router)
    app.include_router(policies_router)
    app.include_router(search_router)
    app.include_router(reports_router)
    app.include_router(admin_router)
    app.include_router(audit_router)
    app.include_router(access_router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    frontend = Path(settings.server.frontend_dir).resolve() if settings.server.frontend_dir else None

    @app.get("/{path:path}", include_in_schema=False)
    def front_end(request: Request, path: str):
        """Serve the single-page app: static files, else index.html.

        Every page has its own URL; unknown paths get index.html and the
        app's router shows the page (or its own "not found" view).
        """
        if path.startswith(("api/", "auth/")):
            raise StarletteHTTPException(404, "Not found.")
        if frontend and path:
            candidate = (frontend / path).resolve()
            # Only files inside the front-end directory, never above it.
            if candidate.is_file() and candidate.is_relative_to(frontend):
                cache = ("public, max-age=31536000, immutable" if path.startswith("assets/")
                         else "no-cache")
                return FileResponse(candidate, headers={"Cache-Control": cache})
        try:
            p = current_principal(request)
        except StarletteHTTPException as exc:
            if exc.status_code == 401:
                return RedirectResponse("/auth/login", 303)
            raise
        if frontend and (frontend / "index.html").is_file():
            return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-store"})
        # No front end built yet: a minimal placeholder.
        roles = ", ".join(sorted(p.grants.roles))
        return _page(f"<p>Signed in as <b>{html.escape(p.user.name)}</b> "
                     f"({html.escape(p.user.uid)}), roles: {html.escape(roles)}.</p>"
                     "<p>The front end has not been built yet.</p>"
                     '<form method="post" action="/auth/logout">'
                     '<button class="btn" type="submit">Sign out</button></form>')

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/") or exc.status_code == 404:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                headers=getattr(exc, "headers", None))
        return _page(f'<p class="err">{html.escape(str(exc.detail))}</p>'
                     '<a class="btn" href="/auth/login">Back to sign-in</a>', exc.status_code)

    return app
