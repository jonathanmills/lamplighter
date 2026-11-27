# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""The Prometheus jobstats source against the dev Prometheus.

Needs the optional services ("devenv.sh prometheus"): a real Prometheus
scraping the simulated exporter (devenv/tools/fake_jobstats_exporter.py).
Skipped when they are not running.
"""

import time

import httpx
import pytest

from lamplighter_webui.config import JobstatsPrometheus
from lamplighter_webui.prometheus import PrometheusSource

pytestmark = pytest.mark.integration
URL = "http://prometheus:9090"


@pytest.fixture(scope="module")
def prom():
    try:
        ready = httpx.get(URL + "/-/ready", timeout=2).status_code == 200
        series = httpx.get(URL + "/api/v1/query", params={"query": "count(lustre_job_stats_total)"},
                           timeout=2).json()["data"]["result"]
    except (httpx.HTTPError, KeyError, ValueError):
        ready, series = False, []
    if not (ready and series):
        pytest.skip("dev Prometheus is not running (devenv.sh prometheus)")
    return PrometheusSource(JobstatsPrometheus(
        url=URL, source_label="server", op_label="operation", metrics={
            "md_ops": "lustre_job_stats_total",
            "read_bytes": "lustre_job_read_bytes_total",
            "write_bytes": "lustre_job_write_bytes_total"}))


def test_live_intervals(prom):
    now = int(time.time())
    ivs, statuses = prom.collect(last=now // 60 * 60 - 180, now=now)
    assert statuses[0].source == "prometheus" and statuses[0].error is None
    by = {(i.source, i.target, i.job_id): i for i in ivs}
    rsync = by[("mds1", "lustre-MDT0000", "rsync.20006")]
    assert rsync.md_ops > 0 and {"getattr", "open"} <= set(rsync.op_counts)
    dd = [i for k, i in by.items() if k[2] == "dd.20005" and "-OST" in k[1]]
    assert len(dd) == 4 and all(i.write_bytes > 0 for i in dd)
    assert {s.source for s in statuses} >= {"prometheus", "mds1", "oss1", "oss2"}
