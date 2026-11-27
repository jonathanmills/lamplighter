# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Directory rollups (collector) and the Browse / coldest-directories APIs."""

import pytest
from sqlalchemy import text

from conftest import oidc_login
from lux_webui.collector import run_once
from lux_webui.db import make_engine

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def run(settings):
    return run_once(settings, make_engine(settings.database), make_engine(settings.store))


@pytest.fixture()
def rows(settings, run):
    with make_engine(settings.store).connect() as c:
        return {r.path: r for r in c.execute(text("SELECT * FROM dir_rollup WHERE run_id = :r"), {"r": run})}


def test_root_totals_equal_every_file(settings, rows):
    with make_engine(settings.database).connect() as c:
        n, size = c.execute(text("SELECT COUNT(*), SUM(size) FROM ENTRIES WHERE type = 'file'")).one()
    root = rows["/data"]
    assert (root.depth, root.files, root.bytes) == (0, n, size)


def test_children_add_up_to_parent(rows):
    for path, r in rows.items():
        kids = [k for k in rows.values() if k.parent_dir == r.dir_id]
        if kids:
            assert sum(k.files for k in kids) <= r.files     # parent may also hold files directly
            assert sum(k.bytes for k in kids) <= r.bytes


def test_depth_limit_and_tracked_paths(settings, rows):
    assert max(r.depth for r in rows.values()) == settings.collector.rollup_depth
    assert "/data/projects/proj-genomics/frank/sequencing-2021" in rows
    assert not any(p.startswith("/data/projects/proj-genomics/frank/sequencing-2021/") for p in rows)


def test_genomics_dataset_is_cold_and_owned_by_frank(rows):
    g = rows["/data/projects/proj-genomics/frank/sequencing-2021"]
    assert g.top_owner == "frank" and g.cold_1y_bytes == g.bytes > 0


@pytest.mark.parametrize("user", ["alice"])   # viewer: paths.view_own only
def test_browse_needs_view_all(client, user, run):
    oidc_login(client, user, "password")
    assert client.get("/api/v1/browse").status_code == 403


def test_browse_down_and_across_the_live_boundary(client, settings, run):
    oidc_login(client, "carol", "password")
    r = client.get("/api/v1/browse").json()
    assert r["dir"]["path"] == "/data" and r["dir"]["totals"]["files"] > 20000
    path = ["projects", "proj-genomics", "frank", "sequencing-2021"]
    for name in path:
        r = client.get("/api/v1/browse", params={"dir": next(s["id"] for s in r["subdirs"] if s["name"] == name)}).json()
    assert r["dir"]["path"] == "/data/projects/proj-genomics/frank/sequencing-2021"
    assert r["subdirs"] and all(s.get("live") for s in r["subdirs"])          # below the rollup depth
    leaf = client.get("/api/v1/browse", params={"dir": r["subdirs"][0]["id"]}).json()
    assert leaf["dir"]["totals"] is None and leaf["files"]["count"] > 0
    assert [a["name"] for a in leaf["ancestors"]] == ["data", *path]
    sizes = [f["size"] for f in leaf["files"]["items"]]
    assert sizes == sorted(sizes, reverse=True)


def test_browse_unknown_dir_is_404(client, run):
    oidc_login(client, "carol", "password")
    assert client.get("/api/v1/browse", params={"dir": "no:such"}).status_code == 404


def test_coldest_directories(client, run):
    oidc_login(client, "carol", "password")
    d = client.get("/api/v1/cold/directories").json()["directories"]
    assert d[0]["path"].endswith("/sequencing-2021") and d[0]["top_owner"] == "frank"
    assert all(a["cold_1y_bytes"] >= b["cold_1y_bytes"] for a, b in zip(d, d[1:]))
    client.cookies.clear()
    oidc_login(client, "alice", "password")
    assert client.get("/api/v1/cold/directories").status_code == 403
