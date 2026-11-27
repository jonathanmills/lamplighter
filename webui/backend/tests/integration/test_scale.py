# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""lamplighter-scale gen: the synthetic database follows Robinhood's rules.

Uses the dev environment's MariaDB root account (test-only credentials)
to create a small scratch database.
"""

import os

import pytest
from sqlalchemy import create_engine, text

from lamplighter_webui.scale import generate

pytestmark = pytest.mark.integration
ROOT = os.environ.get("LAMPLIGHTER_TEST_ROOT_URL", "mysql+pymysql://root:devroot@mariadb")
DB = "robinhood_scale_test"


@pytest.fixture(scope="module")
def db():
    generate(ROOT + "/", "robinhood_dev", DB, 20_000, files_per_dir=50, users=40, groups=5, now=1_795_000_000)
    engine = create_engine(f"{ROOT}/{DB}")
    yield engine
    engine.dispose()
    with create_engine(ROOT + "/", isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS `{DB}`"))


def q(engine, sql):
    with engine.connect() as c:
        return c.execute(text(sql)).all()


def test_counts_and_shape(db):
    [(files, dirs)] = q(db, "SELECT SUM(type = 'file'), SUM(type = 'dir') FROM ENTRIES")
    assert (files, dirs) == (19_600, 400)
    assert q(db, "SELECT COUNT(*) FROM NAMES")[0][0] == 20_000
    top = {r[0] for r in q(db, "SELECT name FROM NAMES WHERE parent_id = '5CA1E:1'")}
    assert top == {b"home", b"projects", b"scratch"}


def test_acct_stat_matches_entries(db):
    [(n, size)] = q(db, "SELECT SUM(count), SUM(size) FROM ACCT_STAT")
    [(n2, size2)] = q(db, "SELECT COUNT(*), SUM(size) FROM ENTRIES")
    assert (n, size) == (n2, size2)
    [(buckets,)] = q(db, "SELECT SUM(sz0+sz1+sz32+sz1K+sz32K+sz1M+sz32M+sz1G+sz32G+sz1T) FROM ACCT_STAT")
    assert buckets == n


def test_names_follow_robinhood(db):
    assert q(db, "SELECT COUNT(*) FROM NAMES WHERE pkn <> SHA1(CONCAT(parent_id, '/', name))")[0][0] == 0
    orphans = q(db, "SELECT COUNT(*) FROM NAMES n LEFT JOIN ENTRIES p ON p.id = n.parent_id "
                    "WHERE n.parent_id <> '5CA1E:1' AND (p.id IS NULL OR p.type <> 'dir')")[0][0]
    assert orphans == 0
    assert q(db, "SELECT COUNT(*) FROM ENTRIES e LEFT JOIN NAMES n ON n.id = e.id WHERE n.id IS NULL")[0][0] == 0


def test_classes_and_alerts_are_consistent(db):
    rows = q(db, "SELECT COUNT(*) FROM ENTRIES WHERE type = 'file' AND "
                 "((size > 20*POW(1024,3)) <> (alert_status = 'alert') OR "
                 "(size > 20*POW(1024,3)) <> (fileclass LIKE '%+outsized+%'))")
    assert rows[0][0] == 0
