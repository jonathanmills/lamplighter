# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Fixtures for integration tests against webui/devenv (run inside the
webui container: ./devenv.sh webui-test)."""

from __future__ import annotations

import html
import os
import re
import ssl

import httpx
import pytest

from lux_webui.config import load_settings
from lux_webui.ldapdir import Directory

pytestmark = pytest.mark.integration

BASE = os.environ.get("LUX_WEBUI_URL", "https://webui.test:8443")
KC = "https://keycloak.test:9443/realms/lamplighter"
CA = "/certs/ca.pem"
SSL = ssl.create_default_context(cafile=CA)


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture()
def directory(settings):
    return Directory(settings.ldap)


@pytest.fixture()
def client():
    with httpx.Client(base_url=BASE, verify=SSL, follow_redirects=True, timeout=20) as c:
        yield c


def post_same_origin(c: httpx.Client, path: str, **kw) -> httpx.Response:
    """A form POST as a browser sends it from our own pages."""
    headers = {"Origin": BASE, **kw.pop("headers", {})}
    return c.post(path, headers=headers, **kw)


def oidc_login(c: httpx.Client, username: str, password: str) -> httpx.Response:
    """Drive the real browser flow: UI -> Keycloak login form -> UI."""
    page = c.get("/auth/oidc")
    assert page.url.host == "keycloak.test", f"expected Keycloak login page, got {page.url}"
    m = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', page.text)
    assert m, "Keycloak login form not found"
    return c.post(html.unescape(m.group(1)),
                  data={"username": username, "password": password, "credentialId": ""})


def client_credentials_token(client_id="lux-checkmk", secret="dev-checkmk-client-secret") -> str:
    r = httpx.post(KC + "/protocol/openid-connect/token", verify=SSL, data={
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": secret})
    r.raise_for_status()
    return r.json()["access_token"]


def password_grant_token(username: str, password: str) -> str:
    r = httpx.post(KC + "/protocol/openid-connect/token", verify=SSL, data={
        "grant_type": "password", "client_id": "lux-webui",
        "client_secret": "dev-webui-client-secret", "username": username, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]
