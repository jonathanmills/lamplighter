# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Fileclass and policy-status counts (collector), against the dev stack.

The dev engine config defines fileclasses and a classifier alert policy
("devenv.sh classify" marks outsized files alert). These tests compare
the collector's figures with Robinhood's ENTRIES table directly.
"""

import pytest
from sqlalchemy import text

from conftest import oidc_login
from lamplighter_webui.collector import run_once
from lamplighter_webui.db import make_engine
from lamplighter_webui.policies import split_classes, status_columns
from lamplighter_webui.store import policy_counts

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def counts(settings):
    run_once(settings, make_engine(settings.database), make_engine(settings.store))
    return policy_counts(make_engine(settings.store))


@pytest.fixture(scope="module")
def rbh(settings):
    return make_engine(settings.database)


def test_status_columns_come_from_the_schema(rbh):
    with rbh.connect() as c:
        assert "alert_status" in status_columns(c)


def test_class_totals_match_entries(counts, rbh):
    with rbh.connect() as c:
        for name, cls in counts["fileclasses"].items():
            if not name:
                continue
            n = c.execute(text("SELECT COUNT(*) FROM ENTRIES WHERE fileclass LIKE :p"),
                          {"p": f"%+{name}+%"}).scalar()
            assert cls["entries"] == n, name


def test_alert_counts_per_class(counts, rbh):
    with rbh.connect() as c:
        alerted = c.execute(text("SELECT COUNT(*) FROM ENTRIES WHERE alert_status = 'alert'")).scalar()
        outsized = c.execute(text("SELECT COUNT(*) FROM ENTRIES WHERE fileclass LIKE '%+outsized+%' "
                                  "AND alert_status = 'alert'")).scalar()
    assert alerted > 0
    assert counts["policies"]["alert"]["statuses"]["alert"]["entries"] == alerted
    assert counts["fileclasses"]["outsized"]["statuses"]["alert"]["alert"] == outsized


def test_every_entry_is_counted_once_per_policy(counts, rbh):
    with rbh.connect() as c:
        total = c.execute(text("SELECT COUNT(*) FROM ENTRIES")).scalar()
    assert sum(s["entries"] for s in counts["policies"]["alert"]["statuses"].values()) == total


def test_split_classes():
    assert split_classes("+a+b+") == ["a", "b"]
    assert split_classes("++") == [""] and split_classes(None) == [""] and split_classes(b"+x+") == ["x"]


def test_policies_api(client, counts):
    oidc_login(client, "alice", "password")             # viewer: aggregates only
    r = client.get("/api/v1/policies")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fileclasses"]["outsized"]["entries"] == counts["fileclasses"]["outsized"]["entries"]
    assert body["filesystem"]["entries"] >= body["fileclasses"]["big_files"]["entries"]
