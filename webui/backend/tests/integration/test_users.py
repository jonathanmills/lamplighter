# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Users & groups: bulk LDAP listings, usage APIs and their scoping."""

import pytest
from sqlalchemy import text

from conftest import oidc_login
from lux_webui.collector import run_once
from lux_webui.db import make_engine

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def fresh_snapshot(settings):
    """A collector run right now, so live figures equal the snapshot."""
    return run_once(settings, make_engine(settings.database), make_engine(settings.store))


# --- LDAP bulk listings ----------------------------------------------------

def test_all_people_from_one_paged_search(directory):
    people = directory.all_people()
    assert set(people) == {"alice", "bob", "carol", "dave", "erin", "frank"}
    assert people["frank"].mail == "frank@lamplighter.test" and people["frank"].uid_number == 20006


def test_all_unix_groups_only_those_with_gid(directory):
    groups = directory.all_unix_groups()
    assert set(groups) == {"proj-astro", "proj-climate", "proj-genomics", "hpc-staff"}
    assert groups["proj-genomics"].members == 1 and groups["proj-astro"].gid_number == 30001


def test_paged_search_survives_small_pages(directory, monkeypatch):
    real = directory._paged
    monkeypatch.setattr(directory, "_paged",
                        lambda conn, base, scope, flt, attrs, page=500: real(conn, base, scope, flt, attrs, page=2))
    directory._bulk.clear()
    assert len(directory.all_people()) == 6   # 3 pages of 2


# --- users list --------------------------------------------------------------

def test_data_manager_sees_every_user_with_ldap_names(client, settings, fresh_snapshot):
    oidc_login(client, "carol", "password")
    body = client.get("/api/v1/usage/users").json()
    assert body["scope"] == "all"
    users = {u["user"]: u for u in body["users"]}
    assert {"alice", "bob", "carol", "dave", "erin", "frank"} <= set(users)
    assert users["frank"]["name"] == "Frank Genome" and users["frank"]["mail"] == "frank@lamplighter.test"
    assert users["bob"]["small_files"] / users["bob"]["files"] > 0.9   # the small-file storm
    with make_engine(settings.database).connect() as c:
        files = c.execute(text("SELECT SUM(count) FROM ACCT_STAT WHERE type = 'file'")).scalar()
    assert sum(u["files"] for u in body["users"]) == files
    assert all(u["change_bytes"] == 0 for u in body["users"])          # just snapshotted


def test_viewer_sees_only_themself(client):
    oidc_login(client, "alice", "password")
    body = client.get("/api/v1/usage/users").json()
    assert body["scope"] == "self" and [u["user"] for u in body["users"]] == ["alice"]


def test_groups_with_descriptions_and_members(client):
    oidc_login(client, "alice", "password")   # viewers may see groups
    groups = {g["group"]: g for g in client.get("/api/v1/usage/groups").json()["groups"]}
    assert groups["proj-genomics"]["description"] == "Genomics project"
    assert groups["proj-genomics"]["members"] == 1
    assert groups["root"]["members"] is None                           # not in LDAP


# --- user detail -------------------------------------------------------------

def test_viewer_can_open_own_page_only(client):
    oidc_login(client, "alice", "password")
    assert client.get("/api/v1/usage/users/alice").status_code == 200
    assert client.get("/api/v1/usage/users/frank").status_code == 403


def test_user_detail_figures_agree(client, fresh_snapshot):
    oidc_login(client, "carol", "password")
    d = client.get("/api/v1/usage/users/frank").json()
    assert sum(b["files"] for b in d["file_sizes"]) == d["files"]
    assert sum(g["bytes"] for g in d["groups"]) == d["bytes"]
    assert [g["group"] for g in d["groups"]] == ["proj-genomics"]
    assert d["history"] and d["history"][-1]["bytes"] == d["bytes"]
    cold = sum(b["bytes"] for b in d["age"]["bands"] if b["band"] >= 4)
    assert cold / d["bytes"] > 0.99


def test_unknown_user_is_404(client):
    oidc_login(client, "carol", "password")
    assert client.get("/api/v1/usage/users/nobody").status_code == 404
