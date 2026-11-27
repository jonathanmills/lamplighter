# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""End-to-end: OIDC and LDAP logins, ACLs, sessions, monitoring API."""

import httpx
import pytest

from conftest import (BASE, SSL, client_credentials_token, oidc_login, password_grant_token,
                      post_same_origin)

pytestmark = pytest.mark.integration


# --- OIDC single sign-on ------------------------------------------------

@pytest.mark.parametrize("user, roles", [
    ("dave", ["admin"]), ("carol", ["data-manager"]), ("alice", ["viewer"]),
])
def test_oidc_login_maps_ldap_groups_to_roles(client, user, roles):
    r = oidc_login(client, user, "password")
    assert r.url.host == "webui.test" and r.url.path == "/", r.url
    assert 'id="root"' in r.text  # the front end's index.html
    me = client.get("/api/v1/me").json()
    assert me["uid"] == user and me["roles"] == roles and me["login_method"] == "oidc"


def test_oidc_user_without_role_is_refused(client):
    r = oidc_login(client, "frank", "password")      # in proj-genomics only
    assert r.status_code == 403 and "no access" in r.text
    assert client.get("/api/v1/me").status_code == 401


def test_oidc_wrong_password_stays_at_keycloak(client):
    r = oidc_login(client, "carol", "wrong")
    assert r.url.host == "keycloak.test"
    assert client.get("/api/v1/me").status_code == 401


def test_session_cookie_is_secure_httponly_lax(client):
    oidc_login(client, "alice", "password")
    raw = [h for h in client.cookies.jar if h.name == "lux_session"]
    assert raw and raw[0].secure
    rest = {k.lower(): v for k, v in raw[0]._rest.items()}
    assert "httponly" in rest and rest.get("samesite") == "lax"


def test_no_tokens_in_cookie(client):
    oidc_login(client, "alice", "password")
    cookie = client.cookies.get("lux_session")
    import base64, json
    payload = json.loads(base64.b64decode(cookie.split(".")[0] + "=="))
    assert set(payload) == {"sid"}, payload


def test_logout_ends_session_and_goes_to_keycloak(client):
    oidc_login(client, "alice", "password")
    client.follow_redirects = False
    r = post_same_origin(client, "/auth/logout")
    assert r.status_code == 303
    loc = httpx.URL(r.headers["location"])
    assert loc.host == "keycloak.test" and loc.path.endswith("/logout")
    assert "id_token_hint" in loc.params
    client.follow_redirects = True
    assert client.get("/api/v1/me").status_code == 401


# --- direct LDAP login --------------------------------------------------

def test_ldap_form_login(client):
    r = post_same_origin(client, "/auth/ldap", data={"username": "dave", "password": "password"})
    assert r.url.path == "/" and 'id="root"' in r.text
    assert client.get("/api/v1/me").json()["login_method"] == "ldap"


def test_ldap_form_wrong_password(client):
    r = post_same_origin(client, "/auth/ldap", data={"username": "dave", "password": "nope"})
    assert r.url.path == "/auth/login" and "Wrong username or password" in r.text
    assert client.get("/api/v1/me").status_code == 401


def test_cross_origin_post_is_refused(client):
    r = client.post("/auth/ldap", data={"username": "dave", "password": "password"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/auth/ldap", data={"username": "dave", "password": "password"})
    assert r.status_code == 403  # no Origin or Referer at all


# --- permissions --------------------------------------------------------

def test_api_requires_login(client):
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/usage/summary").status_code == 401


def test_viewer_sees_usage_summary(client):
    oidc_login(client, "alice", "password")
    body = client.get("/api/v1/usage/summary").json()
    assert {u["user"] for u in body["top_users"]} >= {"erin", "frank"}


def test_security_headers_forbid_framing(client):
    h = client.get("/auth/login").headers
    assert h["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in h["content-security-policy"]


# --- monitoring API (Checkmk) -------------------------------------------

def test_monitoring_with_checkmk_token():
    tok = client_credentials_token()
    r = httpx.get(BASE + "/api/v1/monitoring", verify=SSL, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["database"]["reachable"] is True
    assert body["lamplighter"]["last_scan"]["status"] == "done"
    assert body["lamplighter"]["last_scan"]["entries"] > 1000
    assert "file" in body["usage"]["by_type"]


@pytest.mark.parametrize("auth", [None, "Bearer", "Bearer not-a-jwt", "Basic Zm9vOmJhcg=="])
def test_monitoring_rejects_missing_or_bad_tokens(auth):
    headers = {"Authorization": auth} if auth else {}
    r = httpx.get(BASE + "/api/v1/monitoring", verify=SSL, headers=headers)
    assert r.status_code == 401


def test_monitoring_rejects_a_user_token():
    tok = password_grant_token("dave", "password")   # an admin, but a person
    r = httpx.get(BASE + "/api/v1/monitoring", verify=SSL, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (401, 403)


def test_monitoring_rejects_tampered_token():
    tok = client_credentials_token()
    head, payload, sig = tok.split(".")
    tampered = ".".join([head, payload, sig[:-4] + ("AAAA" if not sig.endswith("AAAA") else "BBBB")])
    r = httpx.get(BASE + "/api/v1/monitoring", verify=SSL, headers={"Authorization": f"Bearer {tampered}"})
    assert r.status_code == 401


# --- serving the front end ----------------------------------------------

@pytest.mark.parametrize("path", ["/", "/cold", "/admin/access", "/no/such/page"])
def test_pages_redirect_to_login_when_signed_out(client, path):
    client.follow_redirects = False
    r = client.get(path)
    assert r.status_code == 303 and r.headers["location"] == "/auth/login"


@pytest.mark.parametrize("path", ["/", "/cold", "/admin/access", "/no/such/page"])
def test_every_page_url_serves_the_app_when_signed_in(client, path):
    oidc_login(client, "carol", "password")
    r = client.get(path)
    assert r.status_code == 200 and 'id="root"' in r.text
    assert r.headers["cache-control"] == "no-store"


def test_static_assets_are_served_and_cached(client):
    js = client.get("/theme-init.js")
    assert js.status_code == 200 and "data-theme" in js.text
    oidc_login(client, "alice", "password")
    index = client.get("/").text
    import re
    asset = re.search(r'src="(/assets/[^"]+\.js)"', index).group(1)
    r = client.get(asset)
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]


@pytest.mark.parametrize("path", ["/..%2f..%2fetc/passwd", "/assets/..%2f..%2f..%2fetc/passwd",
                                  "/%2e%2e/%2e%2e/devenv/webui/session.secret"])
def test_path_traversal_serves_nothing_secret(client, path):
    oidc_login(client, "dave", "password")
    r = client.get(path)
    assert "root:" not in r.text and "dev-session-secret" not in r.text


def test_unknown_api_path_is_json_404(client):
    oidc_login(client, "dave", "password")
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_status_endpoint_for_viewers(client):
    oidc_login(client, "alice", "password")
    body = client.get("/api/v1/status").json()
    assert body["last_scan"]["status"] == "done"


def test_usage_has_file_size_buckets(client):
    oidc_login(client, "alice", "password")
    sizes = client.get("/api/v1/usage/summary").json()["file_sizes"]
    assert [b["bucket"] for b in sizes] == ["< 1 KiB", "1 KiB – 1 MiB", "1 MiB – 1 GiB", "≥ 1 GiB"]
    assert sum(b["files"] for b in sizes) > 20000
