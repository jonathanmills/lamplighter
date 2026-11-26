# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Directory admin page: permissions, no secret leaks, diagnostics."""

import pytest

from conftest import BASE, oidc_login, post_same_origin
from rbh_webui.ldapdiag import test_servers as run_ldap_test
from rbh_webui.ldapdir import Directory

pytestmark = pytest.mark.integration

ENDPOINTS = ["/api/v1/admin/directory/config", "/api/v1/admin/directory/mappings",
             "/api/v1/admin/directory/lookup?user=carol"]


@pytest.mark.parametrize("user", ["alice", "carol"])   # viewer, data manager
def test_only_admins(client, user):
    oidc_login(client, user, "password")
    for path in ENDPOINTS:
        assert client.get(path).status_code == 403, path
    assert post_same_origin(client, "/api/v1/admin/directory/test").status_code == 403


def test_config_never_contains_secrets(client):
    oidc_login(client, "dave", "password")
    body = client.get("/api/v1/admin/directory/config").text
    for secret in ("webui-bind-secret", "dev-webui-client-secret", "dev-session-secret", "rbh_webui\n"):
        assert secret not in body
    cfg = client.get("/api/v1/admin/directory/config").json()
    assert cfg["ldap"]["users"]["filter"] == "(&(objectClass=posixAccount)(uid={username}))"
    assert cfg["ldap"]["bind_password_file"].endswith("ldap-bind.secret")


def test_connection_test_passes_every_step(client):
    oidc_login(client, "dave", "password")
    r = post_same_origin(client, "/api/v1/admin/directory/test").json()
    assert r["ok"]
    steps = {s["step"]: s for s in r["servers"][0]["steps"]}
    assert set(steps) == {"Connect and TLS", "Server certificate", "Service bind", "Search users", "Search groups"}
    assert steps["Server certificate"]["cert"]["subject"] == "CN=ldap.test,O=Robinhood Dev"
    assert steps["Server certificate"]["cert"]["days_left"] > 0


def test_connection_test_explains_a_bad_password(settings, tmp_path):
    (tmp_path / "pw").write_text("wrong\n")
    cfg = settings.ldap.model_copy(update={"bind_password_file": str(tmp_path / "pw")})
    [srv] = run_ldap_test(Directory(cfg))
    assert not srv["ok"]
    assert srv["steps"][-1]["step"] == "Service bind" and "refused" in srv["steps"][-1]["detail"]


def test_connection_test_explains_an_untrusted_certificate(settings, tmp_path):
    import subprocess
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=Other", "-keyout", str(tmp_path / "k"), "-out", str(tmp_path / "ca.pem")],
                   check=True, capture_output=True)
    cfg = settings.ldap.model_copy(update={"ca_file": str(tmp_path / "ca.pem")})
    [srv] = run_ldap_test(Directory(cfg))
    assert not srv["ok"] and srv["steps"][-1]["step"] == "Connect and TLS"


@pytest.mark.parametrize("user, roles, can", [
    ("dave", ["admin"], True), ("carol", ["data-manager"], True), ("frank", [], False),
])
def test_lookup_explains_roles(client, user, roles, can):
    oidc_login(client, "dave", "password")
    r = client.get(f"/api/v1/admin/directory/lookup?user={user}").json()
    assert r["roles"] == roles and r["can_sign_in"] is can
    granting = [g for g in r["groups"] if g["roles"]]
    assert all(g["via"] in ("role mapping", "bootstrap admin group") for g in granting)


def test_lookup_unknown_user(client):
    oidc_login(client, "dave", "password")
    assert client.get("/api/v1/admin/directory/lookup?user=nobody").status_code == 404


def test_mappings_checked_against_directory(client):
    oidc_login(client, "dave", "password")
    rows = client.get("/api/v1/admin/directory/mappings").json()["mappings"]
    assert all(r["exists"] for r in rows)
    viewers = next(r for r in rows if r["group"].startswith("cn=rbh-viewers"))
    assert viewers["members"] == 2 and viewers["roles"] == ["viewer"]
