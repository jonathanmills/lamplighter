# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Lustre jobstats for the triage view.

Lustre keeps per-job counters on its servers (``mdt.*.job_stats`` and
``obdfilter.*.job_stats``). They are cumulative, and an entry is dropped
after ``job_cleanup_interval`` of inactivity, so figures for a time
window come from the difference between consecutive snapshots.

Sources (doc/webui-design.md section 5.4):

* **spool** (default): each Lustre server writes snapshots with a cron
  one-liner into a directory, by any transport. The files are untrusted
  input: strict names, size limits, regular files only, parsed as text
  and never executed. See ``SpoolSource``.
* **prometheus**: for sites that already scrape jobstats (prometheus.py).

Both produce ``Interval`` records, stored by ``store_intervals``. Job IDs
are kept as Lustre labels them (``jobid_name``). No job scheduler is
involved; scheduler details are an optional, opt-in hook (``JobDetails``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from .config import JobstatsConfig, Settings, load_settings
from .db import make_engine
from .store import migrate

log = logging.getLogger(__name__)

# --- parsing ---------------------------------------------------------------

# "mdt.lustre-MDT0000.job_stats=" (lctl get_param without -n)
_TARGET = re.compile(r"^(?:mdt|obdfilter|osd-[a-z]+)\.([A-Za-z0-9_-]{1,64})\.job_stats=\s*$")
_JOB = re.compile(r"^-\s+job_id:\s*(.*?)\s*$")
_SNAP = re.compile(r"^\s+snapshot_time:\s*([0-9]+(?:\.[0-9]+)?)")
_OP = re.compile(r"^\s+([a-z_]{1,32}):\s*\{(.*)\}\s*$")
_FIELD = re.compile(r"\b(samples|sum|unit):\s*([A-Za-z0-9_]+)")

MAX_JOB_ID = 255

# Counters: op -> (samples, sum). Only "reqs" and "bytes" units are kept;
# latency histograms ("usecs") are not traffic.
Counters = dict[str, tuple[int, int]]


def _job_id(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        raw = raw[1:-1]
    # control characters never belong in a label shown in a browser
    raw = "".join(ch if ch.isprintable() else "?" for ch in raw)
    return raw[:MAX_JOB_ID]


def parse_snapshot(content: str) -> dict[str, dict[str, Counters]]:
    """Parse ``lctl get_param mdt.*.job_stats obdfilter.*.job_stats``.

    Returns {target: {job_id: {op: (samples, sum)}}}. Lines it does not
    recognise are ignored, so newer Lustre fields do no harm.
    """
    out: dict[str, dict[str, Counters]] = {}
    target: str | None = None
    job: Counters | None = None
    for line in content.splitlines():
        m = _TARGET.match(line)
        if m:
            target = m.group(1)
            out.setdefault(target, {})
            job = None
            continue
        if target is None:
            continue
        m = _JOB.match(line)
        if m:
            job = out[target].setdefault(_job_id(m.group(1)), {})
            continue
        if job is None or _SNAP.match(line):
            continue
        m = _OP.match(line)
        if m:
            fields = dict(_FIELD.findall(m.group(2)))
            if fields.get("unit") not in ("reqs", "bytes"):
                continue
            try:
                samples = int(fields.get("samples", "0"))
                total = int(fields.get("sum", "0"))
            except ValueError:
                continue
            job[m.group(1)] = (samples, total)
    return out


def is_ost(target: str) -> bool:
    return "-OST" in target


# --- intervals ---------------------------------------------------------------

@dataclass
class Interval:
    source: str
    target: str
    job_id: str
    t_start: int
    t_end: int
    md_ops: int = 0        # metadata requests (MDT targets)
    read_bytes: int = 0
    write_bytes: int = 0
    read_ops: int = 0
    write_ops: int = 0
    other_ops: int = 0     # other OST requests (punch, setattr, sync...)
    op_counts: dict[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.md_ops or self.read_bytes or self.write_bytes
                    or self.read_ops or self.write_ops or self.other_ops)


def _delta(cur: tuple[int, int], prev: tuple[int, int] | None) -> tuple[int, int]:
    """Counter difference; a new counter counts from zero."""
    if prev is None:
        return cur
    return cur[0] - prev[0], cur[1] - prev[1]


def intervals_between(source: str, t0: int, prev: dict[str, dict[str, Counters]],
                      t1: int, cur: dict[str, dict[str, Counters]]) -> list[Interval]:
    """Activity between two snapshots of the same server."""
    out = []
    for target, jobs in cur.items():
        before = prev.get(target, {})
        ost = is_ost(target)
        for job_id, ops in jobs.items():
            iv = Interval(source, target, job_id, t0, t1)
            old = before.get(job_id, {})
            # If any counter went down, the entry was dropped and recreated
            # (or cleared) in between: count all of it from zero.
            reset = any(op in old and (v[0] < old[op][0] or v[1] < old[op][1])
                        for op, v in ops.items())
            for op, value in ops.items():
                samples, total = _delta(value, None if reset else old.get(op))
                if op in ("read_bytes", "write_bytes"):
                    if op == "read_bytes":
                        iv.read_bytes += total
                        iv.read_ops += samples
                    else:
                        iv.write_bytes += total
                        iv.write_ops += samples
                elif samples:
                    if ost:
                        iv.other_ops += samples
                    else:
                        iv.md_ops += samples
                    iv.op_counts[op] = iv.op_counts.get(op, 0) + samples
            if not iv.empty:
                out.append(iv)
    return out


# --- the spool directory -------------------------------------------------------

# <host>.<unix time>.jobstats, written by the server (atomically: .tmp, then mv)
_SPOOL_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9.-]{0,127})\.([0-9]{9,11})\.jobstats(\.done)?$")


@dataclass
class SourceStatus:
    source: str
    last_snapshot: int | None = None
    targets: list[str] = field(default_factory=list)
    jobs: int = 0
    error: str | None = None


class SpoolSource:
    """Reads snapshot files dropped by the Lustre servers.

    Each run processes new files per host in time order, against the
    newest already-processed one (kept as ``*.jobstats.done``), then
    keeps only that newest file as the next baseline.
    """

    def __init__(self, directory: str, max_file_bytes: int, max_interval_seconds: int):
        self.dir = Path(directory)
        self.max_file_bytes = max_file_bytes
        self.max_interval = max_interval_seconds

    def _read(self, path: Path) -> str:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError("not a regular file")
            if st.st_size > self.max_file_bytes:
                raise ValueError(f"larger than {self.max_file_bytes} bytes")
            with os.fdopen(fd, "rb") as fh:
                fd = -1
                data = fh.read(self.max_file_bytes + 1)
        finally:
            if fd >= 0:
                os.close(fd)
        if len(data) > self.max_file_bytes:
            raise ValueError(f"larger than {self.max_file_bytes} bytes")
        return data.decode("utf-8", errors="replace")

    def _reject(self, path: Path, why: str, status: SourceStatus) -> None:
        log.warning("jobstats spool: rejecting %s: %s", path.name, why)
        status.error = f"{path.name}: {why}"
        try:
            path.rename(path.with_name(path.name + ".rejected"))
        except OSError as exc:
            log.warning("jobstats spool: cannot rename %s: %s", path.name, exc)

    def collect(self) -> tuple[list[Interval], list[SourceStatus]]:
        hosts: dict[str, list[tuple[int, bool, Path]]] = {}
        try:
            names = os.listdir(self.dir)
        except OSError as exc:
            raise RuntimeError(f"cannot read spool directory {self.dir}: {exc}") from exc
        for name in names:
            m = _SPOOL_NAME.match(name)
            if m:
                hosts.setdefault(m.group(1), []).append((int(m.group(2)), bool(m.group(3)), self.dir / name))

        intervals: list[Interval] = []
        statuses: list[SourceStatus] = []
        for host, files in sorted(hosts.items()):
            files.sort()
            status = SourceStatus(host)
            done = [f for f in files if f[1]]
            new = [f for f in files if not f[1]]
            base: tuple[int, dict, Path] | None = None
            if done:
                t, _, path = done[-1]
                try:
                    base = (t, parse_snapshot(self._read(path)), path)
                except (OSError, ValueError) as exc:
                    self._reject(path, str(exc), status)
            for t, _, path in new:
                try:
                    snap = parse_snapshot(self._read(path))
                except (OSError, ValueError) as exc:
                    self._reject(path, str(exc), status)
                    continue
                if base is not None and t <= base[0]:
                    log.info("jobstats spool: %s is not newer than its baseline; dropped", path.name)
                    path.unlink(missing_ok=True)
                    continue
                if base is not None and t - base[0] <= self.max_interval:
                    intervals.extend(intervals_between(host, base[0], base[1], t, snap))
                done_path = path.with_name(path.name + ".done")
                path.rename(done_path)
                base = (t, snap, done_path)
            # keep only the newest processed snapshot as the next baseline
            for name in os.listdir(self.dir):
                m = _SPOOL_NAME.match(name)
                if m and m.group(1) == host and m.group(3) and (base is None or name != base[2].name):
                    (self.dir / name).unlink(missing_ok=True)
            if base is not None:
                status.last_snapshot = base[0]
                status.targets = sorted(base[1])
                status.jobs = sum(len(j) for j in base[1].values())
            statuses.append(status)
        return intervals, statuses


# --- storage --------------------------------------------------------------------

def store_intervals(engine: Engine, intervals: list[Interval], statuses: list[SourceStatus],
                    retention_hours: int, now: int | None = None) -> int:
    now = now or int(time.time())
    with engine.begin() as c:
        if intervals:
            c.execute(text(
                "INSERT INTO jobstats_interval (source, target, job_id, t_start, t_end, md_ops, "
                "read_bytes, write_bytes, read_ops, write_ops, other_ops, op_counts) VALUES "
                "(:source, :target, :job_id, :t_start, :t_end, :md_ops, :read_bytes, :write_bytes, "
                ":read_ops, :write_ops, :other_ops, :op_counts)"),
                [{**{k: getattr(iv, k) for k in (
                    "source", "target", "job_id", "t_start", "t_end", "md_ops", "read_bytes",
                    "write_bytes", "read_ops", "write_ops", "other_ops")},
                  "op_counts": json.dumps(iv.op_counts, sort_keys=True)} for iv in intervals])
        for s in statuses:
            c.execute(text(
                "INSERT INTO jobstats_sources (source, last_snapshot, targets, jobs, last_error) "
                "VALUES (:s, :t, :tg, :j, :e) ON DUPLICATE KEY UPDATE "
                "last_snapshot = COALESCE(VALUES(last_snapshot), last_snapshot), "
                "targets = IF(VALUES(last_snapshot) IS NULL, targets, VALUES(targets)), "
                "jobs = IF(VALUES(last_snapshot) IS NULL, jobs, VALUES(jobs)), "
                "last_error = VALUES(last_error), checked_at = CURRENT_TIMESTAMP"),
                {"s": s.source, "t": s.last_snapshot, "tg": ",".join(s.targets), "j": s.jobs, "e": s.error})
        c.execute(text("DELETE FROM jobstats_interval WHERE t_end < :cut"),
                  {"cut": now - retention_hours * 3600})
    return len(intervals)


# --- reads for the triage view --------------------------------------------------

_SUMS = "SUM(md_ops), SUM(read_bytes), SUM(write_bytes), SUM(read_ops), SUM(write_ops), SUM(other_ops)"


def _figures(row) -> dict:
    md, rb, wb, ro, wo, oo = (int(v or 0) for v in row)
    return {"md_ops": md, "read_bytes": rb, "write_bytes": wb, "read_ops": ro,
            "write_ops": wo, "other_ops": oo}


def source_status(engine: Engine, stale_after: int, now: int) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(text("SELECT source, last_snapshot, targets, jobs, last_error "
                              "FROM jobstats_sources ORDER BY source")).all()
    out = []
    for src, snap, targets, jobs, err in rows:
        age = now - int(snap) if snap is not None else None
        out.append({"source": src, "last_snapshot": int(snap) if snap is not None else None,
                    "age_seconds": age, "stale": age is None or age > stale_after,
                    "targets": [t for t in (targets or "").split(",") if t], "jobs": int(jobs),
                    "error": err})
    return out


def triage_jobs(engine: Engine, window: int, sort: str, limit: int, now: int) -> dict:
    """Busiest jobs, targets and a time series over the last ``window`` seconds."""
    cut = now - window
    order = {"md": "SUM(md_ops)", "io": "SUM(read_bytes) + SUM(write_bytes)"}[sort]
    step = max(60, window // 60)
    with engine.connect() as c:
        jobs = c.execute(text(
            f"SELECT job_id, {_SUMS}, COUNT(DISTINCT target) FROM jobstats_interval "
            f"WHERE t_end > :cut GROUP BY job_id ORDER BY {order} DESC, job_id LIMIT :n"),
            {"cut": cut, "n": limit}).all()
        targets = c.execute(text(
            f"SELECT target, {_SUMS}, COUNT(DISTINCT job_id) FROM jobstats_interval "
            f"WHERE t_end > :cut GROUP BY target ORDER BY target"), {"cut": cut}).all()
        series = c.execute(text(
            f"SELECT FLOOR(t_end / :step) * :step AS b, {_SUMS} FROM jobstats_interval "
            f"WHERE t_end > :cut GROUP BY b ORDER BY b"), {"cut": cut, "step": step}).all()
        totals = c.execute(text(f"SELECT {_SUMS}, MAX(t_end) FROM jobstats_interval WHERE t_end > :cut"),
                           {"cut": cut}).one()
        ops: dict[str, dict[str, int]] = {}
        if jobs:
            for job_id, counts in c.execute(
                    text("SELECT job_id, op_counts FROM jobstats_interval "
                         "WHERE t_end > :cut AND job_id IN :ids").bindparams(
                        bindparam("ids", expanding=True)),
                    {"cut": cut, "ids": [j[0] for j in jobs]}):
                merged = ops.setdefault(job_id, {})
                for op, n in json.loads(counts or "{}").items():
                    merged[op] = merged.get(op, 0) + int(n)
    return {
        "window": window, "step": step, "now": now,
        "as_of": int(totals[6]) if totals[6] is not None else None,
        "totals": _figures(totals[:6]),
        "jobs": [{"job_id": j[0], **_figures(j[1:7]), "targets": int(j[7]),
                  "top_ops": sorted(ops.get(j[0], {}).items(), key=lambda kv: -kv[1])[:4]} for j in jobs],
        "targets": [{"target": t[0], **_figures(t[1:7]), "jobs": int(t[7])} for t in targets],
        "series": [{"t": int(r[0]), **_figures(r[1:7])} for r in series],
    }


# --- job details hook ------------------------------------------------------------

class JobDetails:
    """Optional scheduler details for job IDs (the opt-in plug-in hook).

    The default adds nothing. A scheduler plug-in (Slurm first, later)
    would subclass this, be selected explicitly in config.yaml, and be the
    only code that talks to that scheduler.
    """

    name = "none"

    def details(self, job_ids: list[str]) -> dict[str, dict]:
        return {}


def job_details_provider(name: str) -> JobDetails:
    if name == "none":
        return JobDetails()
    raise ValueError(f"unknown job details provider {name!r}")


# --- lux-jobstats ------------------------------------------------------------------

def collect(cfg: JobstatsConfig, store: Engine) -> tuple[list[Interval], list[SourceStatus]]:
    if cfg.source == "prometheus":
        from .prometheus import PrometheusSource
        with store.connect() as c:
            last = c.execute(text("SELECT last_snapshot FROM jobstats_sources WHERE source = :s"),
                             {"s": PrometheusSource.STATE}).scalar()
        return PrometheusSource(cfg.prometheus).collect(int(last) if last is not None else None)
    sp = cfg.spool
    return SpoolSource(sp.directory, sp.max_file_bytes, sp.max_interval_seconds).collect()


def run_once(settings: Settings) -> int:
    """One pass: read new jobstats, store intervals, apply retention."""
    if settings.jobstats is None:
        log.info("jobstats are not configured (config.yaml: jobstats); nothing to do")
        return 0
    store = make_engine(settings.store)
    migrate(store)
    with store.connect() as lock:
        # one run at a time; released explicitly (pooled connections outlive us)
        if lock.execute(text("SELECT GET_LOCK('lux_jobstats', 0)")).scalar() != 1:
            log.info("another lux-jobstats run is in progress; skipping")
            return 0
        try:
            intervals, statuses = collect(settings.jobstats, store)
            n = store_intervals(store, intervals, statuses, settings.jobstats.retention_hours)
            log.info("stored %d intervals from %d sources", n, len(statuses))
            return n
        finally:
            lock.execute(text("SELECT RELEASE_LOCK('lux_jobstats')"))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="lux-jobstats",
                                 description="Read Lustre jobstats for Lamplighter's triage view.")
    ap.add_argument("--config", help="config file (default: $LUX_WEBUI_CONFIG)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)
    try:
        run_once(load_settings(args.config))
    except Exception as exc:
        log.error("lux-jobstats failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
