# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""General data export (export.py): every table as CSV or JSON, with the
same rows the page shows, formula-safe CSV, and the export permission rule."""

import csv
import io
import types

import pytest
from fastapi import HTTPException

from conftest import oidc_login
from lamplighter_webui.acl import Perm
from lamplighter_webui.collector import run_once
from lamplighter_webui.db import make_engine
from lamplighter_webui.export import _cell, require_export

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def run(settings):
    return run_once(settings, make_engine(settings.database), make_engine(settings.store))


def download(client, path, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].startswith('attachment; filename="lamplighter-')
    if params.get("format") == "json":
        return r.json()["rows"]
    return list(csv.DictReader(io.StringIO(r.text)))


def test_tables_export_the_rows_the_page_shows(client, run):
    oidc_login(client, "carol", "password")                 # data manager: cold.export
    users = client.get("/api/v1/usage/users").json()["users"]
    rows = download(client, "/api/v1/usage/users", format="csv")
    assert [r["user"] for r in rows] == [u["user"] for u in users]
    assert "mail" in rows[0]
    assert len(download(client, "/api/v1/usage/groups", format="json")) == len(client.get("/api/v1/usage/groups").json()["groups"])
    fc = download(client, "/api/v1/policies", format="csv", table="fileclasses")
    pol = download(client, "/api/v1/policies", format="json", table="policies")
    data = client.get("/api/v1/policies").json()
    assert {r["fileclass"] for r in fc} == set(data["fileclasses"])
    assert len(pol) == sum(len(v["statuses"]) for v in data["policies"].values())
    cold = download(client, "/api/v1/cold/directories", format="csv")
    assert cold and all(r["path"].startswith("/") for r in cold)
    files = download(client, "/api/v1/browse", format="json", table="files",
                     dir=client.get("/api/v1/cold/directories").json()["directories"][0]["id"])
    assert all(f["path"].startswith("/data/") for f in files)
    found = download(client, "/api/v1/search", format="json", owner="frank", min_size=1)
    assert found and all(r["owner"] == "frank" for r in found)
    old = client.get("/api/v1/search.csv", params={"owner": "frank", "min_size": 1})   # old link still works
    assert old.status_code == 200 and old.text.startswith("path,type,owner")


def test_viewer_exports_aggregates_and_her_own_files(client, run):
    oidc_login(client, "alice", "password")                 # viewer: no cold.export
    me = download(client, "/api/v1/usage/users", format="csv")
    assert [r["user"] for r in me] == ["alice"]             # her own row (with her own address)
    assert download(client, "/api/v1/policies", format="json")
    top = client.get("/api/v1/browse").json()
    d = top["subdirs"][0]["id"]
    rows = download(client, "/api/v1/browse", format="csv", table="subdirs", dir=d)
    assert all(r.get("top_owner", "") == "" for r in rows)  # still no other person's name
    assert client.get("/api/v1/cold/directories", params={"format": "csv"}).status_code == 403   # no cold.view


def test_export_rules_and_formula_safety():
    def principal(*perms):
        return types.SimpleNamespace(user=types.SimpleNamespace(uid="x"),
                                     grants=types.SimpleNamespace(allows=lambda p: p in perms))
    require_export(principal(Perm.COLD_EXPORT), "lists of paths")
    with pytest.raises(HTTPException) as e:
        require_export(principal(Perm.PATHS_VIEW_ALL), "lists of paths")
    assert e.value.status_code == 403
    assert _cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and _cell("-1") == "'-1" and _cell(-1) == -1
    assert _cell(None) == "" and _cell(["a", "b"]) == '["a", "b"]' and _cell(b"x") == "x"
