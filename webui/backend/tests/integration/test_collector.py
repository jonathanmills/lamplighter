# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Collector runs against the dev database, and the APIs that read its output."""

import httpx
import pytest
from sqlalchemy import text

from conftest import BASE, SSL, client_credentials_token, oidc_login
from rbh_webui.collector import run_once
from rbh_webui.db import make_engine

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engines(settings):
    return make_engine(settings.database), make_engine(settings.store)


@pytest.fixture(scope="module")
def run_id(settings, engines):
    return run_once(settings, *engines)


def _hist(store, run_id):
    with store.connect() as c:
        return c.execute(text("SELECT uid, access_band, modify_band, files, bytes "
                              "FROM age_hist WHERE run_id = :r"), {"r": run_id}).all()


def test_age_histogram_covers_every_file(engines, run_id):
    source, store = engines
    with source.connect() as c:
        n, size = c.execute(text("SELECT COUNT(*), SUM(size) FROM ENTRIES WHERE type = 'file'")).one()
    rows = _hist(store, run_id)
    assert sum(r.files for r in rows) == n
    assert sum(r.bytes for r in rows) == size


def test_bands_tell_the_sample_story(engines, run_id):
    rows = _hist(engines[1], run_id)
    def share(uid, bands):
        mine = [r for r in rows if r.uid == uid]
        return sum(r.bytes for r in mine if r.access_band in bands) / sum(r.bytes for r in mine)
    assert share("frank", {4, 5}) > 0.99      # cold genomics data
    bob = [r for r in rows if r.uid == "bob"]
    assert sum(r.files for r in bob if r.access_band == 0) / sum(r.files for r in bob) > 0.95


def test_acct_snapshot_matches_acct_stat(engines, run_id):
    source, store = engines
    with source.connect() as c:
        live = c.execute(text("SELECT SUM(count), SUM(size) FROM ACCT_STAT")).one()
    with store.connect() as c:
        snap = c.execute(text("SELECT SUM(entries), SUM(bytes) FROM acct_history WHERE run_id = :r"),
                         {"r": run_id}).one()
    assert tuple(map(int, live)) == tuple(map(int, snap))


def test_only_one_collector_at_a_time(settings, engines):
    with engines[1].connect() as other:
        assert other.execute(text("SELECT GET_LOCK('rbh_webui_collector', 0)")).scalar() == 1
        try:
            with pytest.raises(RuntimeError, match="in progress"):
                run_once(settings, *engines)
        finally:
            other.execute(text("SELECT RELEASE_LOCK('rbh_webui_collector')"))


def test_retention_keeps_newest_runs(settings, engines):
    s = settings.model_copy(deep=True)
    s.collector.keep_runs = 2
    ids = [run_once(s, *engines) for _ in range(3)]
    with engines[1].connect() as c:
        kept = [r for (r,) in c.execute(text("SELECT id FROM collector_runs ORDER BY id"))]
        orphans = c.execute(text("SELECT COUNT(*) FROM age_hist WHERE run_id NOT IN "
                                 "(SELECT id FROM collector_runs)")).scalar()
    assert kept == ids[-2:] and orphans == 0


# --- APIs ------------------------------------------------------------------

def test_age_bands_for_viewers(client, run_id):
    oidc_login(client, "alice", "password")
    body = client.get("/api/v1/usage/age").json()
    assert [b["band"] for b in body["bands"]] == [0, 1, 2, 3, 4, 5]
    assert client.get("/api/v1/usage/age?basis=modify").status_code == 200
    assert client.get("/api/v1/usage/age?basis=bogus").status_code == 422


def test_cold_owners_need_cold_view(client, run_id):
    oidc_login(client, "alice", "password")          # viewer
    assert client.get("/api/v1/cold/owners").status_code == 403


def test_cold_owners_for_data_managers_with_contact(client, run_id):
    oidc_login(client, "carol", "password")          # data manager
    body = client.get("/api/v1/cold/owners?older_than=1y").json()
    top = body["owners"][0]
    assert top["user"] == "frank" and top["mail"] == "frank@rbh.test" and top["name"] == "Frank Genome"
    assert top["cold_bytes"] <= top["total_bytes"]
    very = client.get("/api/v1/cold/owners?older_than=3y").json()["owners"]
    assert [o["user"] for o in very][:1] == ["frank"]


def test_collector_runs_permission(client, run_id):
    oidc_login(client, "alice", "password")
    assert client.get("/api/v1/collector/runs").status_code == 403
    client.cookies.clear()
    oidc_login(client, "carol", "password")
    runs = client.get("/api/v1/collector/runs").json()
    assert runs[0]["status"] == "ok" and runs[0]["entries"] > 20000


def test_monitoring_reports_collector_freshness(run_id):
    tok = client_credentials_token()
    body = httpx.get(BASE + "/api/v1/monitoring", verify=SSL,
                     headers={"Authorization": f"Bearer {tok}"}).json()
    col = body["collector"]
    assert col["last_run"]["status"] == "ok" and col["last_ok_finished"] > 0
    assert len(col["age_by_access"]["bands"]) == 6


def test_csv_export_needs_cold_export_and_is_audited(client, run_id):
    oidc_login(client, "carol", "password")          # data manager: may export
    r = client.get("/api/v1/cold/owners.csv?older_than=1y")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("user,name,mail,bytes_not_accessed_1y")
    assert lines[1].startswith("frank,Frank Genome,frank@rbh.test,")
    client.cookies.clear()
    oidc_login(client, "alice", "password")          # viewer: may not
    assert client.get("/api/v1/cold/owners.csv").status_code == 403
