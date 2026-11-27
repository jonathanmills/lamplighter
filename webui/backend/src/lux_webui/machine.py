# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Machine clients (Checkmk): OAuth 2.0 bearer tokens from Keycloak.

The Checkmk special agent obtains a short-lived access token with the
client-credentials grant and sends it as "Authorization: Bearer ...".
We verify its signature against the realm's JWKS, plus its issuer,
expiry, audience and the required client role.
"""

from __future__ import annotations

import ssl
import threading
from dataclasses import dataclass

import httpx
import jwt
from fastapi import HTTPException, Request

from .audit import audit
from .config import MonitoringConfig, OIDCConfig


@dataclass(frozen=True)
class MachineClient:
    client_id: str
    roles: frozenset[str]


class TokenVerifier:
    def __init__(self, oidc: OIDCConfig, mon: MonitoringConfig):
        self.issuer = oidc.issuer.rstrip("/")
        self.ca_file = oidc.ca_file
        self.mon = mon
        self._jwks: jwt.PyJWKClient | None = None
        self._lock = threading.Lock()

    def _jwks_client(self) -> jwt.PyJWKClient:
        with self._lock:
            if self._jwks is None:
                ctx = ssl.create_default_context(cafile=self.ca_file)
                meta = httpx.get(self.issuer + "/.well-known/openid-configuration",
                                 verify=ctx, timeout=10).raise_for_status().json()
                if meta.get("issuer") != self.issuer:
                    raise RuntimeError("issuer in discovery document does not match config")
                self._jwks = jwt.PyJWKClient(meta["jwks_uri"], ssl_context=ctx,
                                             cache_keys=True, lifespan=300)
            return self._jwks

    def verify(self, token: str) -> MachineClient:
        key = self._jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256", "ES256", "PS256"],
                            audience=self.mon.audience, issuer=self.issuer,
                            options={"require": ["exp", "iat", "iss", "aud"]})
        roles = frozenset(claims.get("resource_access", {})
                          .get(self.mon.audience, {}).get("roles", []))
        client_id = claims.get("azp") or claims.get("client_id") or "?"
        return MachineClient(client_id=client_id, roles=roles)


def monitoring_client(request: Request) -> MachineClient:
    """Dependency: a verified machine client holding the monitoring role."""
    verifier: TokenVerifier | None = request.app.state.token_verifier
    if verifier is None:
        raise HTTPException(404)
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Bearer token required.",
                            headers={"WWW-Authenticate": 'Bearer realm="lamplighter-monitoring"'})
    try:
        client = verifier.verify(token)
    except jwt.PyJWTError as exc:
        audit("monitoring", outcome="denied", client=request.client.host if request.client else None,
              reason=f"invalid token: {exc}")
        raise HTTPException(401, "Invalid token.",
                            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
    if verifier.mon.required_role not in client.roles:
        audit("monitoring", outcome="denied", client=request.client.host if request.client else None,
              reason="missing role", client_id=client.client_id)
        raise HTTPException(403, f"Token lacks the {verifier.mon.required_role!r} role.")
    return client
