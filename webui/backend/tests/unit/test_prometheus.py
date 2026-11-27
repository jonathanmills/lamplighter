# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""The Prometheus jobstats source, against a fake Prometheus."""

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from lux_webui.config import JobstatsConfig, JobstatsPrometheus
from lux_webui.prometheus import PrometheusSource

NOW = 1795000030          # collect() aligns to the minute: t1 = 1795000020 // 60 * 60


def cfg(**kw):
    return JobstatsPrometheus(url="https://prom.test:9090", op_label="operation", metrics={
        "md_ops": 'lustre_job_stats_total{component="mdt"}',
        "read_bytes": "lustre_job_read_bytes_total",
        "write_bytes": "lustre_job_write_bytes_total"}, **kw)


def vector(*samples):
    return {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": m, "value": [NOW, v]} for m, v in samples]}}


class FakeProm:
    def __init__(self, answers, fail=False):
        self.answers, self.fail, self.queries = answers, fail, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)
        self.queries.append((q["query"][0], int(q["time"][0])))
        if self.fail:
            return httpx.Response(503, text="unavailable")
        for needle, answer in self.answers.items():
            if needle in q["query"][0]:
                return httpx.Response(200, json=answer)
        return httpx.Response(200, json=vector())


def source(fake, c=None):
    return PrometheusSource(c or cfg(), transport=httpx.MockTransport(fake))


def test_queries_and_intervals():
    fake = FakeProm({
        "lustre_job_stats_total": vector(
            ({"jobid": "find.20003", "target": "fs-MDT0000", "instance": "mds1:9169", "operation": "getattr"}, "6000"),
            ({"jobid": "find.20003", "target": "fs-MDT0000", "instance": "mds1:9169", "operation": "statfs"}, "2.4")),
        "lustre_job_write_bytes_total": vector(
            ({"jobid": "dd.20005", "target": "fs-OST0001", "instance": "oss1:9169"}, "1048576"),
            ({"jobid": "dd.20005", "target": "fs-OST0002", "instance": "oss1:9169"}, "0"))})
    ivs, st = source(fake).collect(last=1794999900, now=NOW)
    t1 = NOW // 60 * 60
    assert fake.queries[0] == ('sum by (jobid, target, instance, operation) '
                               '(increase(lustre_job_stats_total{component="mdt"}[120s]))', t1)
    assert all(t == t1 for _, t in fake.queries) and len(fake.queries) == 3
    by = {(i.source, i.target, i.job_id): i for i in ivs}
    find = by[("mds1:9169", "fs-MDT0000", "find.20003")]
    assert (find.md_ops, find.op_counts, find.t_start, find.t_end) == (6002, {"getattr": 6000, "statfs": 2}, 1794999900, t1)
    assert by[("oss1:9169", "fs-OST0001", "dd.20005")].write_bytes == 1048576
    assert ("oss1:9169", "fs-OST0002", "dd.20005") not in by           # zero increase
    states = {s.source: s for s in st}
    assert states["prometheus"].last_snapshot == t1 and states["prometheus"].error is None
    assert states["mds1:9169"].targets == ["fs-MDT0000"]


def test_first_run_and_long_gap_use_one_minute():
    fake = FakeProm({})
    source(fake).collect(last=None, now=NOW)
    source(fake).collect(last=NOW - 86400, now=NOW)                   # gap beyond max_interval
    assert all(q.endswith("[60s]))") for q, _ in fake.queries)


def test_outage_keeps_the_previous_time():
    fake = FakeProm({}, fail=True)
    ivs, st = source(fake).collect(last=1794999900, now=NOW)
    assert ivs == [] and st[0].last_snapshot is None and "503" in st[0].error


def test_hostile_values_are_ignored():
    fake = FakeProm({"lustre_job_read_bytes_total": vector(
        ({"jobid": "x", "target": "fs-OST0000", "instance": "oss1"}, "NaN"),
        ({"jobid": "y", "target": "fs-OST0000", "instance": "oss1"}, "+Inf"),
        ({"jobid": "z\x1b[2J", "target": "fs-OST0000", "instance": "oss1"}, "-5"),
        ({"jobid": "w\x1b", "target": "fs-OST0000", "instance": "oss1"}, "10"),
        ({"target": "fs-OST0000", "instance": "oss1"}, "10"))})        # no job label
    ivs, _ = source(fake).collect(last=None, now=NOW)
    assert [(i.job_id, i.read_bytes) for i in ivs] == [("w?", 10)]


def test_unexpected_answer_is_an_error():
    fake = FakeProm({"lustre_job_stats_total": {"status": "error", "error": "parse error"}})
    _, st = source(fake).collect(last=None, now=NOW)
    assert "parse error" in st[0].error


def test_prometheus_source_needs_metrics():
    with pytest.raises(ValueError):
        JobstatsConfig(source="prometheus")
    with pytest.raises(ValueError):
        JobstatsConfig(source="prometheus", prometheus=JobstatsPrometheus(url="https://p"))
    assert JobstatsConfig(source="prometheus", prometheus=cfg()).source == "prometheus"
