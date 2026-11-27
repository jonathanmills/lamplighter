# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Jobstats ingestion (spool) and the triage API, against the dev stack.

The spool is filled by webui/devenv/tools/gen_jobstats.py: simulated
servers mds1, oss1 and oss2 (quiet for the last 20 minutes), with a
getattr storm by find.20003 (carol) in the last 30 minutes and steady
writes by dd.20005 (erin).
"""

import subprocess
import sys

import pytest
from sqlalchemy import text

from conftest import oidc_login
from lamplighter_webui.db import make_engine
from lamplighter_webui.jobstats import run_once

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ingested(settings):
    with make_engine(settings.store).begin() as c:
        c.execute(text("DELETE FROM jobstats_interval"))
        c.execute(text("DELETE FROM jobstats_sources"))
    subprocess.run([sys.executable, "/devenv/tools/gen_jobstats.py", settings.jobstats.spool.directory],
                   check=True, capture_output=True)
    return run_once(settings)


def triage(client, **params):
    r = client.get("/api/v1/triage", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_ingestion_stored_intervals(ingested):
    assert ingested > 1000


def test_metadata_storm_tops_the_list_with_its_owner(client, ingested):
    oidc_login(client, "dave", "password")
    js = triage(client, window="1h", sort="md")["jobstats"]
    assert js["configured"] and js["source"] == "spool" and js["job_details"] == "none"
    top = js["jobs"][0]
    assert top["job_id"] == "find.20003"
    assert top["parts"] == {"process": "find", "uid": "20003"}
    assert top["user"] == "carol" and top["name"]
    assert top["top_ops"][0][0] == "getattr"
    assert top["details"] == {}                      # no scheduler plug-in by default
    assert js["step"] == 60 and len(js["series"]) >= 50


def test_io_sort_and_targets(client, ingested):
    oidc_login(client, "dave", "password")
    js = triage(client, window="1h", sort="io")["jobstats"]
    assert js["jobs"][0]["job_id"] == "dd.20005"
    assert js["jobs"][0]["user"] == "erin"
    assert {t["target"] for t in js["targets"]} == {
        "lustre-MDT0000", "lustre-OST0000", "lustre-OST0001", "lustre-OST0002", "lustre-OST0003"}
    root = next(j for j in js["jobs"] if j["job_id"] == "lfs.0")
    assert root["user"] is None                       # UID 0 is not a directory person


def test_quiet_server_is_stale(client, ingested):
    oidc_login(client, "dave", "password")
    sources = {s["source"]: s for s in triage(client)["jobstats"]["sources"]}
    assert not sources["mds1"]["stale"] and not sources["oss1"]["stale"]
    assert sources["oss2"]["stale"] and sources["oss2"]["age_seconds"] >= 1200


def test_robinhood_side_is_present(client, ingested):
    oidc_login(client, "dave", "password")
    rh = triage(client)["robinhood"]
    assert "last_scan" in rh and "recent" in rh


def test_needs_triage_permission(client, ingested):
    oidc_login(client, "carol", "password")          # data manager: no triage.view
    assert client.get("/api/v1/triage").status_code == 403


def test_rejects_unknown_window(client, ingested):
    oidc_login(client, "dave", "password")
    assert client.get("/api/v1/triage", params={"window": "1y"}).status_code == 422
