# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Scheduled reports (reports.py, reports_api.py): definitions, runs as the
owner, history and graphs, email to permitted recipients only, and the
permission rules. Email goes to the dev stack's mailpit."""

import csv
import io

import httpx
import pytest
from sqlalchemy import text

from conftest import oidc_login, post_same_origin
from lamplighter_webui import reports as R
from lamplighter_webui.collector import run_once
from lamplighter_webui.db import make_engine

pytestmark = pytest.mark.integration
MAILPIT = "http://mailpit:8025/api/v1"


@pytest.fixture(scope="module")
def run(settings):
    return run_once(settings, make_engine(settings.database), make_engine(settings.store))


@pytest.fixture()
def clean(settings):
    def wipe():
        with make_engine(settings.store).begin() as c:
            c.execute(text("DELETE FROM reports"))
        httpx.delete(MAILPIT + "/messages")
    wipe()
    yield
    wipe()


def send(client, method, path, json=None):
    return client.request(method, path, json=json, headers={"Origin": str(client.base_url).rstrip("/")})


COLD = {"name": "Cold owners", "source": "cold-owners", "params": {"older_than": "1y"}, "schedule": "weekly",
        "keep_runs": 3, "recipients": ["carol", "alice"],
        "chart": {"value": "bytes_not_accessed_1y", "stack_by": "user", "top": 2}}


def test_report_runs_keeps_history_graphs_and_emails(client, run, clean):
    oidc_login(client, "carol", "password")                      # data manager: reports.manage
    r = send(client, "POST", "/api/v1/reports", COLD)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    for _ in range(4):
        out = send(client, "POST", f"/api/v1/reports/{rid}/run").json()
        assert out["status"] == "ok" and out["rows"] > 0
    rep = client.get(f"/api/v1/reports/{rid}").json()
    assert len(rep["runs"]) == 3                                  # keep_runs
    expected = client.get("/api/v1/cold/owners", params={"format": "json", "limit": 200}).json()["rows"]
    assert rep["latest"]["rows"] == expected                      # the same table as the page download
    s = rep["series"]
    top2 = [x["user"] for x in sorted(expected, key=lambda x: -x["bytes_not_accessed_1y"])[:2]]
    assert len(s["times"]) == 3 and [x["name"] for x in s["series"]][:2] == top2
    assert s["series"][-1]["name"] == "Other" and s["series"][0]["values"][0] == max(
        x["bytes_not_accessed_1y"] for x in expected)
    assert all(len(x["values"]) == 3 for x in s["series"])
    # email: carol may see it, alice (viewer) may not
    assert "emailed to 1" in out["message"] and "alice (may not see this report)" in out["message"]
    msgs = httpx.get(MAILPIT + "/messages").json()["messages"]
    assert msgs and all([t["Address"] for t in m["To"]] == ["carol@lamplighter.test"] for m in msgs)
    assert msgs[0]["Subject"] == "Lamplighter report: Cold owners" and msgs[0]["Attachments"] == 1
    # download a run
    d = client.get(f"/api/v1/reports/{rid}/runs/{rep['runs'][0]['id']}", params={"format": "csv"})
    assert d.status_code == 200 and list(csv.DictReader(io.StringIO(d.text)))[0]["user"] == expected[0]["user"]


def test_viewers_cannot_see_or_manage_reports_beyond_their_permissions(client, run, clean, settings):
    oidc_login(client, "carol", "password")
    rid = send(client, "POST", "/api/v1/reports", {**COLD, "recipients": []}).json()["id"]
    gid = send(client, "POST", "/api/v1/reports", {"name": "Groups", "source": "groups", "schedule": "daily"}).json()["id"]
    send(client, "POST", f"/api/v1/reports/{gid}/run")
    client.cookies.clear()
    oidc_login(client, "alice", "password")                       # viewer: aggregates only
    names = [r["name"] for r in client.get("/api/v1/reports").json()["reports"]]
    assert names == ["Groups"]                                    # not the cold-owner report
    assert client.get(f"/api/v1/reports/{rid}").status_code == 404
    assert client.get(f"/api/v1/reports/{gid}").json()["latest"]["rows"]
    assert send(client, "POST", "/api/v1/reports", {"name": "x", "source": "groups"}).status_code == 403
    assert send(client, "POST", f"/api/v1/reports/{gid}/run").status_code == 403
    assert send(client, "DELETE", f"/api/v1/reports/{gid}").status_code == 403
    assert [s["key"] for s in client.get("/api/v1/reports/sources").json()["sources"]] == [
        "groups", "fileclasses", "policy-status"]


def test_validation(client, run, clean):
    oidc_login(client, "carol", "password")
    ok = {"name": "Groups", "source": "groups", "schedule": "daily"}
    assert send(client, "POST", "/api/v1/reports", ok).status_code == 200
    assert send(client, "POST", "/api/v1/reports", ok).status_code == 409                # same name
    for bad in ({**ok, "name": "a", "schedule": "yearly"}, {**ok, "name": "b", "source": "nope"},
                {**ok, "name": "c", "keep_runs": 0}, {**ok, "name": "d", "recipients": ["nobody-here"]},
                {"name": "e", "source": "cold-owners", "params": {"older_than": "5y"}},
                {"name": "f", "source": "groups", "params": {"extra": 1}}):
        r = send(client, "POST", "/api/v1/reports", bad)
        assert r.status_code == 422, (bad, r.text)


def test_runs_as_owner_and_is_denied_when_they_lose_access(settings, run, clean):
    state = R.make_state(settings)
    d = R.validate(settings, {"name": "Search", "source": "search", "schedule": "manual",
                              "params": {"path": "/data/projects", "owner": "frank", "min_size": 1}})
    rid = R.save_report(state.store_engine, d, "carol")
    out = R.run(state, R.get_report(state.store_engine, rid), "test")
    assert out["status"] == "ok" and out["rows"] > 0
    rows = R.run_data(state.store_engine, rid)["rows"]
    assert all(x["path"].startswith("/data/projects/") and x["owner"] == "frank" for x in rows)
    with state.store_engine.begin() as c:
        c.execute(text("UPDATE reports SET owner = 'alice' WHERE id = :i"), {"i": rid})
    out = R.run(state, R.get_report(state.store_engine, rid), "test")
    assert out["status"] == "denied" and "reports.manage" in out["message"]


def test_whole_filesystem_search_report_and_schedule(settings, run, clean):
    state = R.make_state(settings)
    d = R.validate(settings, {"name": "Big", "source": "search", "schedule": "hourly",
                              "params": {"path": "/data", "min_size": 10 * 1024**3}})
    rid = R.save_report(state.store_engine, d, "carol")
    assert R.run(state, R.get_report(state.store_engine, rid), "test")["status"] == "ok"
    now = 1_800_000_000
    reps = [{"schedule": "hourly", "last_run_at": now - 3500}, {"schedule": "hourly", "last_run_at": now - 3000},
            {"schedule": "daily", "last_run_at": None}, {"schedule": "manual", "last_run_at": None}]
    assert R.due(reps, now) == [reps[0], reps[2]]                 # 5 minutes of slack for timer jitter
    assert R.run_due(settings) == 0                               # just ran: nothing due
