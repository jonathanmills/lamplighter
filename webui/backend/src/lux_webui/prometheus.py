# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Jobstats from Prometheus, for sites that already scrape them.

The alternative to the spool directory (doc/webui-design.md section 5.4).
Each lux-jobstats run asks Prometheus for the increase of the configured
counters since the previous run, grouped by job, target and server, and
turns the answer into the same Interval records as the spool.

Exporters name metrics and labels differently, so both are mapped in
config.yaml (jobstats.prometheus). Prometheus' increase() handles
counter resets and extrapolation.
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path

import httpx

from .config import JobstatsPrometheus
from .jobstats import MAX_JOB_ID, Interval, SourceStatus, _job_id

log = logging.getLogger(__name__)


class PrometheusSource:
    STATE = "prometheus"        # jobstats_sources row holding the last query time

    def __init__(self, cfg: JobstatsPrometheus, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        headers = {}
        if cfg.bearer_token_file:
            headers["Authorization"] = "Bearer " + Path(cfg.bearer_token_file).read_text().strip()
        self.http = httpx.Client(base_url=cfg.url.rstrip("/"), headers=headers, timeout=cfg.timeout_seconds,
                                 verify=cfg.ca_file or True, transport=transport)

    def _query(self, promql: str, at: int) -> list[dict]:
        r = self.http.get("/api/v1/query", params={"query": promql, "time": str(at)})
        if r.status_code == 400:
            # Prometheus explains a bad query (e.g. a wrong metric mapping) in the body.
            try:
                reason = r.json().get("error", "")
            except ValueError:
                reason = r.text[:200]
            raise ValueError(f"Prometheus rejected {promql!r}: {reason}")
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "success" or body.get("data", {}).get("resultType") != "vector":
            raise ValueError(f"unexpected answer: {body.get('error') or body.get('status')}")
        return body["data"]["result"]

    def collect(self, last: int | None, now: int | None = None) -> tuple[list[Interval], list[SourceStatus]]:
        c = self.cfg
        t1 = (now or int(time.time())) // 60 * 60
        t0 = last if last is not None and t1 - c.max_interval_seconds <= last < t1 else t1 - 60
        state = SourceStatus(self.STATE)
        if t1 <= t0:
            return [], [state]
        span = t1 - t0
        by = [c.job_label, c.target_label, c.source_label]
        found: dict[tuple[str, str, str], Interval] = {}
        servers: dict[str, set[str]] = {}
        try:
            for field_name, selector in c.metrics.items():
                group = by + ([c.op_label] if field_name == "md_ops" and c.op_label else [])
                promql = f"sum by ({', '.join(group)}) (increase({selector}[{span}s]))"
                for sample in self._query(promql, t1):
                    labels = sample.get("metric", {})
                    job = _job_id(str(labels.get(c.job_label, "")))[:MAX_JOB_ID]
                    target = str(labels.get(c.target_label, ""))[:64]
                    source = str(labels.get(c.source_label, "prometheus"))[:255]
                    try:
                        value = float(sample["value"][1])
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
                    if not job or not target or not math.isfinite(value) or value <= 0:
                        continue
                    n = int(round(value))
                    iv = found.setdefault((source, target, job), Interval(source, target, job, t0, t1))
                    setattr(iv, field_name, getattr(iv, field_name) + n)
                    if field_name == "md_ops" and c.op_label and labels.get(c.op_label):
                        op = str(labels[c.op_label])[:32]
                        iv.op_counts[op] = iv.op_counts.get(op, 0) + n
                    servers.setdefault(source, set()).add(target)
        except (httpx.HTTPError, ValueError) as exc:
            # Keep the previous query time, so the next run covers this gap.
            state.error = f"{type(exc).__name__}: {exc}"[:1000]
            log.warning("prometheus: %s", state.error)
            return [], [state]
        state.last_snapshot = t1
        statuses = [state] + [SourceStatus(s, last_snapshot=t1, targets=sorted(t),
                                           jobs=len({k[2] for k in found if k[0] == s}))
                              for s, t in sorted(servers.items())]
        return [iv for iv in found.values() if not iv.empty], statuses
