# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Jobstats summaries (migration 006) give the same triage figures as
summing the raw intervals: every window, unaligned edges, several batches."""

import random

import pytest
from sqlalchemy import text

from lamplighter_webui.db import make_engine
from lamplighter_webui.jobstats import FIGURES, Interval, store_intervals, triage_jobs

pytestmark = pytest.mark.integration
TABLES = ("job_minute", "job_hour", "target_minute", "target_hour", "total_minute", "presence")
NOW = 1_795_000_000 + 1_234          # not aligned to a minute or an hour


def _clear(engine):
    with engine.begin() as c:
        for t in TABLES:
            c.execute(text(f"DELETE FROM jobstats_{t}"))


@pytest.fixture
def store(settings):
    engine = make_engine(settings.store)
    _clear(engine)
    yield engine
    _clear(engine)
    engine.dispose()


def _intervals(rng):
    out = []
    targets = [f"lustre-MDT{i:04X}" for i in range(2)] + [f"lustre-OST{i:04X}" for i in range(6)]
    for _ in range(3000):
        t_end = NOW - rng.randrange(26 * 3600)
        target = rng.choice(targets)
        md = "MDT" in target
        out.append(Interval(
            source="mds1" if md else "oss1", target=target, job_id=f"job.{rng.randrange(40)}",
            t_start=t_end - 60, t_end=t_end,
            md_ops=rng.randrange(1, 1000) if md else 0,
            read_bytes=0 if md else rng.randrange(1 << 30), write_bytes=0 if md else rng.randrange(1 << 30),
            read_ops=0 if md else rng.randrange(500), write_ops=0 if md else rng.randrange(500),
            other_ops=rng.randrange(20),
            op_counts={rng.choice(["open", "getattr", "close", "punch"]): rng.randrange(1, 50)}))
    return out


def _expected(intervals, window, sort, limit):
    m0 = (NOW - window) // 60 * 60
    inside = [iv for iv in intervals if iv.t_end >= m0]
    jobs: dict[str, dict] = {}
    for iv in inside:
        j = jobs.setdefault(iv.job_id, {k: 0 for k in FIGURES} | {"t": set(), "ops": {}})
        for k in FIGURES:
            j[k] += getattr(iv, k)
        j["t"].add(iv.target)
        for op, n in iv.op_counts.items():
            j["ops"][op] = j["ops"].get(op, 0) + n
    key = (lambda kv: (-kv[1]["md_ops"], kv[0])) if sort == "md" else \
          (lambda kv: (-(kv[1]["read_bytes"] + kv[1]["write_bytes"]), kv[0]))
    top = sorted(jobs.items(), key=key)[:limit]
    per_target: dict[str, set] = {}
    for iv in inside:
        per_target.setdefault(iv.target, set()).add(iv.job_id)
    return ([(job, {k: v[k] for k in FIGURES}, len(v["t"]), v["ops"]) for job, v in top],
            {t: len(js) for t, js in per_target.items()},
            {k: sum(getattr(iv, k) for iv in inside) for k in FIGURES})


def test_summaries_match_raw_intervals(store):
    rng = random.Random(7)
    intervals = _intervals(rng)
    rng.shuffle(intervals)
    for i in range(0, len(intervals), 700):           # several runs: merging must add up
        store_intervals(store, intervals[i:i + 700], [], retention_hours=168, now=NOW)
    for window in (900, 3600, 6 * 3600, 86400):
        for sort in ("md", "io"):
            got = triage_jobs(store, window, sort, 25, NOW)
            top, per_target, totals = _expected(intervals, window, sort, 25)
            assert [j["job_id"] for j in got["jobs"]] == [t[0] for t in top], (window, sort)
            for j, (_, figs, n_targets, ops) in zip(got["jobs"], top):
                assert {k: j[k] for k in FIGURES} == figs
                assert j["targets"] == n_targets
                assert all(ops[op] == n for op, n in j["top_ops"])          # ties may order either way
                assert [n for _, n in j["top_ops"]] == sorted(ops.values(), reverse=True)[:4]
            assert {t["target"]: t["jobs"] for t in got["targets"]} == per_target
            assert got["totals"] == totals
            assert {k: sum(s[k] for s in got["series"]) for k in FIGURES} == totals


def test_retention_drops_old_detail(store):
    old = Interval("oss1", "lustre-OST0000", "job.1", NOW - 30 * 3600 - 60, NOW - 30 * 3600, write_bytes=5)
    store_intervals(store, [old], [], retention_hours=168, now=NOW)
    with store.connect() as c:
        n = {t: c.execute(text(f"SELECT COUNT(*) FROM jobstats_{t}")).scalar() for t in TABLES}
    assert n == {"job_minute": 0, "job_hour": 1, "target_minute": 0, "target_hour": 1, "total_minute": 0,
                 "presence": 0}
