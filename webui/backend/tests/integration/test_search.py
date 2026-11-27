# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Directory-scoped file search, checked against ENTRIES directly."""

import time

import pytest
from sqlalchemy import text

from conftest import oidc_login
from lamplighter_webui.db import make_engine
from lamplighter_webui.search_api import MAX_RESULTS, glob_to_like

pytestmark = pytest.mark.integration
GiB = 1 << 30


@pytest.fixture(scope="module")
def rbh(settings):
    return make_engine(settings.database)


def find(client, **params):
    r = client.get("/api/v1/search", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_owner_and_size_match_the_database(client, rbh):
    oidc_login(client, "dave", "password")
    out = find(client, owner="erin", min_size=20 * GiB)
    with rbh.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM ENTRIES WHERE uid = 'erin' AND type = 'file' "
                           "AND size >= :s"), {"s": 20 * GiB}).scalar()
    assert out["complete"] and len(out["results"]) == n > 0
    assert out["dir"]["path"] == "/data"
    assert all(r["owner"] == "erin" and r["size"] >= 20 * GiB and r["path"].startswith("/data/") for r in out["results"])
    sizes = [r["size"] for r in out["results"]]
    assert sizes == sorted(sizes, reverse=True)


def test_age_filter_matches_the_database(client, rbh):
    oidc_login(client, "dave", "password")
    out = find(client, owner="frank", not_accessed_days=730)
    with rbh.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM ENTRIES WHERE uid = 'frank' AND type = 'file' "
                           "AND last_access < :t"), {"t": int(time.time()) - 730 * 86400}).scalar()
    assert len(out["results"]) == min(n, MAX_RESULTS) and n > 0


def test_scoped_to_a_directory(client):
    oidc_login(client, "dave", "password")
    root = client.get("/api/v1/browse").json()
    scratch = next(d for d in root["subdirs"] if d["path"].endswith("/scratch"))
    out = find(client, dir=scratch["id"], type="any")
    assert out["dir"]["path"] == "/data/scratch"
    assert out["results"] and all(r["path"].startswith("/data/scratch/") for r in out["results"])


def test_name_pattern(client):
    oidc_login(client, "dave", "password")
    out = find(client, name="part0000?.dat")
    assert out["results"] and all(r["path"].rsplit("/", 1)[1][:8] == "part0000" for r in out["results"])


def test_stops_at_the_result_limit_and_says_so(client):
    oidc_login(client, "dave", "password")
    out = find(client)                                    # every file: more than the limit
    assert not out["complete"] and out["stopped"] == "result limit"
    assert len(out["results"]) == MAX_RESULTS


def test_permissions_and_export(client):
    oidc_login(client, "alice", "password")               # viewer: no paths.view_all
    assert client.get("/api/v1/search").status_code == 403
    client.cookies.clear()
    oidc_login(client, "carol", "password")               # data manager: may export
    r = client.get("/api/v1/search.csv", params={"owner": "erin", "min_size": 20 * GiB})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("path,type,owner")


def test_unknown_directory(client):
    oidc_login(client, "dave", "password")
    assert client.get("/api/v1/search", params={"dir": "no-such-id"}).status_code == 404


def test_glob_to_like():
    assert glob_to_like("a_b*.h5") == "a\\_b%.h5"
    assert glob_to_like("50%?") == "50\\%_"
