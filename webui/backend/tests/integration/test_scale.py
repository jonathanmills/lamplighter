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


def test_rollup_same_with_and_without_covering_index(db):
    """The rollup's two paths (temporary id-ordered copy of NAMES, or a
    replica's covering index on NAMES (id, parent_id)) agree exactly."""
    from lamplighter_webui import rollup

    def run():
        with db.connect() as c:
            return sorted(rollup.compute(c, 1_795_000_000, 4, "/scale"), key=lambda r: r["dir_id"])

    with db.connect() as c:
        assert rollup.covering_index(c) is None
    plain = run()
    with db.connect() as c:
        c.execute(text("ALTER TABLE NAMES ADD INDEX names_by_id (id, parent_id)"))
    try:
        with db.connect() as c:
            assert rollup.covering_index(c) == "names_by_id"
        assert run() == plain
    finally:
        with db.connect() as c:
            c.execute(text("ALTER TABLE NAMES DROP INDEX names_by_id"))
    root = next(r for r in plain if r["depth"] == 0)
    [(files, size)] = q(db, "SELECT COUNT(*), SUM(size) FROM ENTRIES WHERE type = 'file'")
    assert (root["files"], root["bytes"]) == (files, size)


def test_split_covers_every_row_once(db):
    from lamplighter_webui.parallel import split, where
    with db.connect() as c:
        ranges = split(c, 8)
        assert ranges[0][0] is None and ranges[-1][1] is None and len(ranges) > 1
        assert all(ranges[i][1] == ranges[i + 1][0] for i in range(len(ranges) - 1))
        counts = []
        for r in ranges:
            cond, params = where(r)
            counts.append(c.execute(text(f"SELECT COUNT(*) FROM ENTRIES WHERE {cond}"), params).scalar())
        assert sum(counts) == c.execute(text("SELECT COUNT(*) FROM ENTRIES")).scalar()


def test_parallel_scans_match_serial(db):
    from lamplighter_webui.collector import _age_histogram
    from lamplighter_webui.policies import collect_counts_parallel

    def key(rows):
        return sorted(tuple(sorted(r.items())) for r in rows)

    assert key(_age_histogram(db, 1_795_000_000, 1, None)) == key(_age_histogram(db, 1_795_000_000, 4, None))
    assert key(collect_counts_parallel(db, 1)) == key(collect_counts_parallel(db, 4))


def test_long_fileclass_lists_fall_back_to_exact_counts(db):
    """Fileclasses are grouped on their first 255 bytes for speed; a longer
    list must still be counted exactly (the slower grouping takes over)."""
    from lamplighter_webui.policies import collect_counts, collect_counts_parallel

    def key(rows):
        return sorted(tuple(sorted(r.items())) for r in rows)

    long_list = "+" + "+".join(f"class{i:03d}" for i in range(30)) + "+"      # about 270 bytes
    assert len(long_list) > 255
    with db.connect() as c:
        victim = c.execute(text("SELECT id FROM ENTRIES WHERE type = 'file' LIMIT 1")).scalar()
        old = c.execute(text("SELECT fileclass FROM ENTRIES WHERE id = :i"), {"i": victim}).scalar()
        c.execute(text("UPDATE ENTRIES SET fileclass = :f WHERE id = :i"), {"f": long_list, "i": victim})
        c.commit()
    try:
        parallel = collect_counts_parallel(db, 4)
        with db.connect() as c:
            assert key(parallel) == key(collect_counts(c))
        assert any(r["fileclass"] == "class029" for r in parallel)
    finally:
        with db.connect() as c:
            c.execute(text("UPDATE ENTRIES SET fileclass = :f WHERE id = :i"), {"f": old, "i": victim})
            c.commit()
