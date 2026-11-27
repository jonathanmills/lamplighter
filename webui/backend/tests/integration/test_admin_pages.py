# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Audit log persistence and page API; access-control page API."""

import uuid

import pytest
from sqlalchemy import text

from conftest import oidc_login, post_same_origin
from lux_webui.db import make_engine

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("user", ["alice", "carol"])
def test_admin_pages_need_admin(client, user):
    oidc_login(client, user, "password")
    assert client.get("/api/v1/admin/audit").status_code == 403
    assert client.get("/api/v1/admin/access").status_code == 403


def test_failed_login_is_stored_and_filterable(client):
    who = "nobody-" + uuid.uuid4().hex[:8]
    post_same_origin(client, "/auth/ldap", data={"username": who, "password": "x"})
    oidc_login(client, "dave", "password")
    for _ in range(50):                           # the writer is asynchronous
        r = client.get(f"/api/v1/admin/audit?user={who}&since_hours=1").json()
        if r["items"]:
            break
        import time; time.sleep(0.1)
    [item] = r["items"]
    assert item["event"] == "login" and item["outcome"] == "denied"
    assert item["details"]["reason"] == "bad credentials" and item["client"]


def test_denied_permission_is_audited_with_outcome(client):
    oidc_login(client, "alice", "password")
    client.get("/api/v1/cold/owners")             # alice lacks cold.view
    client.cookies.clear()
    oidc_login(client, "dave", "password")
    import time
    for _ in range(50):
        r = client.get("/api/v1/admin/audit?user=alice&event=denied&since_hours=1").json()
        if r["items"]:
            break
        time.sleep(0.1)
    assert r["items"][0]["outcome"] == "denied"
    assert r["items"][0]["details"]["permission"] == "cold.view"


def test_audit_paging_with_before_id(client):
    oidc_login(client, "dave", "password")
    first = client.get("/api/v1/admin/audit?limit=2&since_hours=24").json()
    assert len(first["items"]) == 2 and first["more"]
    nxt = client.get(f"/api/v1/admin/audit?limit=2&since_hours=24&before_id={first['items'][-1]['id']}").json()
    assert all(i["id"] < first["items"][-1]["id"] for i in nxt["items"])


def test_monitoring_polls_are_not_audited(settings):
    from conftest import BASE, SSL, client_credentials_token
    import httpx, time
    tok = client_credentials_token()
    httpx.get(BASE + "/api/v1/monitoring", verify=SSL, headers={"Authorization": f"Bearer {tok}"})
    time.sleep(0.5)
    with make_engine(settings.store).connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM audit_log WHERE event = 'monitoring' AND outcome = 'ok' "
                           "AND ts > UTC_TIMESTAMP() - INTERVAL 1 MINUTE")).scalar()
    assert n == 0


def test_access_shows_roles_and_resolved_members(client):
    oidc_login(client, "dave", "password")
    a = client.get("/api/v1/admin/access").json()
    roles = {r["name"]: set(r["permissions"]) for r in a["roles"]}
    assert len(roles["admin"]) == len(a["permissions"]) == 12
    assert "cold.export" in roles["data-manager"] and "cold.export" not in roles["viewer"]
    members = {g["group"].split(",")[0]: g["members"] for g in a["groups"]}
    assert members == {"cn=lux-admins": ["dave"], "cn=lux-data-managers": ["carol"],
                       "cn=lux-viewers": ["alice", "erin"]}

