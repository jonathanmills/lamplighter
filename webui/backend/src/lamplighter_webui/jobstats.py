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

Both produce ``Interval`` records, which ``store_intervals`` adds to
per-minute and per-hour summaries (migration 006). Job IDs
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

MINUTE, HOUR = 60, 3600
DETAIL_HOURS = 25          # minute tables and presence: the longest triage window (24 h) + 1 h
TRIAGE_TMP = 64 * 1024**2  # bytes of in-memory grouping per triage request
FIGURES = ("md_ops", "read_bytes", "write_bytes", "read_ops", "write_ops", "other_ops")


def summarize(intervals: list[Interval]) -> tuple[dict, dict, dict, dict]:
    """Fold intervals into totals keyed by (resolution, bucket, job or target),
    presence: (hour, job, target) -> newest snapshot time, and filesystem
    totals per minute. An interval
    counts in the minute and hour holding its end, as before."""
    jobs: dict[tuple, list] = {}
    targets: dict[tuple, list] = {}
    presence: dict[tuple, int] = {}
    totals: dict[int, list] = {}
    for iv in intervals:
        figs = [getattr(iv, k) for k in FIGURES]
        for res, size in (("minute", MINUTE), ("hour", HOUR)):
            b = iv.t_end // size * size
            j = jobs.setdefault((res, b, iv.job_id), [0] * 6 + [{}])
            t = targets.setdefault((res, b, iv.target), [0] * 6 + [0])
            for i, v in enumerate(figs):
                j[i] += v
                t[i] += v
            for op, n in iv.op_counts.items():
                j[6][op] = j[6].get(op, 0) + n
            t[6] = max(t[6], iv.t_end)
        tot = totals.setdefault(iv.t_end // MINUTE * MINUTE, [0] * 7)
        for i, v in enumerate(figs):
            tot[i] += v
        tot[6] = max(tot[6], iv.t_end)
        key = (iv.t_end // HOUR * HOUR, iv.job_id, iv.target)
        presence[key] = max(presence.get(key, 0), iv.t_end)
    return jobs, targets, presence, totals


_ADD = ", ".join(f"{k} = {k} + VALUES({k})" for k in FIGURES)


def store_intervals(engine: Engine, intervals: list[Interval], statuses: list[SourceStatus],
                    retention_hours: int, now: int | None = None) -> int:
    """Add intervals to the per-job and per-target summaries (migration 006).
    Runs under lamplighter-jobstats' lock, so merging op_counts by reading
    and rewriting the touched rows is safe."""
    now = now or int(time.time())
    jobs, targets, presence, totals = summarize(intervals)
    cols = ", ".join(FIGURES)
    vals = ", ".join(f":{k}" for k in FIGURES)
    with engine.begin() as c:
        for res in ("minute", "hour"):
            mine = {k[1:]: v for k, v in jobs.items() if k[0] == res}
            if mine:
                # merge op_counts with what this minute/hour already holds
                old: dict[tuple, dict] = {}
                for bkt, job, ops in c.execute(text(
                        f"SELECT bucket, job_id, op_counts FROM jobstats_job_{res} "
                        "WHERE bucket IN :b AND job_id IN :j").bindparams(
                            bindparam("b", expanding=True), bindparam("j", expanding=True)),
                        {"b": sorted({k[0] for k in mine}), "j": sorted({k[1] for k in mine})}):
                    old[(int(bkt), job)] = json.loads(ops or "{}")
                rows = []
                for (bkt, job), v in mine.items():
                    ops = old.get((bkt, job), {})
                    for op, n in v[6].items():
                        ops[op] = int(ops.get(op, 0)) + n
                    rows.append({"bucket": bkt, "id": job, **dict(zip(FIGURES, v[:6])),
                                 "ops": json.dumps(ops, sort_keys=True)})
                c.execute(text(
                    f"INSERT INTO jobstats_job_{res} (bucket, job_id, {cols}, op_counts) "
                    f"VALUES (:bucket, :id, {vals}, :ops) ON DUPLICATE KEY UPDATE {_ADD}, "
                    "op_counts = VALUES(op_counts)"), rows)
            mine = [{"bucket": k[1], "id": k[2], **dict(zip(FIGURES, v[:6])), "last": v[6]}
                    for k, v in targets.items() if k[0] == res]
            if mine:
                c.execute(text(
                    f"INSERT INTO jobstats_target_{res} (bucket, target, {cols}, last_end) "
                    f"VALUES (:bucket, :id, {vals}, :last) ON DUPLICATE KEY UPDATE {_ADD}, "
                    "last_end = GREATEST(last_end, VALUES(last_end))"), mine)
        if totals:
            c.execute(text(
                f"INSERT INTO jobstats_total_minute (bucket, {cols}, last_end) VALUES (:bucket, {vals}, :last) "
                f"ON DUPLICATE KEY UPDATE {_ADD}, last_end = GREATEST(last_end, VALUES(last_end))"),
                [{"bucket": b, **dict(zip(FIGURES, v[:6])), "last": v[6]} for b, v in totals.items()])
        if presence:
            c.execute(text(
                "INSERT INTO jobstats_presence (hour, job_id, target, last_seen) VALUES (:h, :j, :t, :l) "
                "ON DUPLICATE KEY UPDATE last_seen = GREATEST(last_seen, VALUES(last_seen))"),
                [{"h": h, "j": j, "t": t, "l": last} for (h, j, t), last in presence.items()])
        for s in statuses:
            c.execute(text(
                "INSERT INTO jobstats_sources (source, last_snapshot, targets, jobs, last_error) "
                "VALUES (:s, :t, :tg, :j, :e) ON DUPLICATE KEY UPDATE "
                "last_snapshot = COALESCE(VALUES(last_snapshot), last_snapshot), "
                "targets = IF(VALUES(last_snapshot) IS NULL, targets, VALUES(targets)), "
                "jobs = IF(VALUES(last_snapshot) IS NULL, jobs, VALUES(jobs)), "
                "last_error = VALUES(last_error), checked_at = CURRENT_TIMESTAMP"),
                {"s": s.source, "t": s.last_snapshot, "tg": ",".join(s.targets), "j": s.jobs, "e": s.error})
        detail_cut = now - DETAIL_HOURS * HOUR
        keep_cut = now - max(retention_hours, DETAIL_HOURS) * HOUR
        for table, cut in (("jobstats_job_minute", detail_cut), ("jobstats_target_minute", detail_cut),
                           ("jobstats_total_minute", detail_cut),
                           ("jobstats_job_hour", keep_cut), ("jobstats_target_hour", keep_cut)):
            c.execute(text(f"DELETE FROM {table} WHERE bucket < :cut"), {"cut": cut})
        c.execute(text("DELETE FROM jobstats_presence WHERE hour < :cut"), {"cut": detail_cut})
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


def _window(kind: str) -> list[str]:
    """The two parts of a window (params :m0, :hstart): the partial first
    hour from the minute table, whole hours from the hourly one."""
    return [f"FROM jobstats_{kind}_minute WHERE bucket >= :m0 AND bucket < :hstart",
            f"FROM jobstats_{kind}_hour WHERE bucket >= :hstart"]


def _window_sums(c, kind: str, key: str, w: dict) -> dict[str, list[int]]:
    """Figures per job or target over a window. Each part is grouped on its
    own and the parts are added here: a UNION of the raw rows would be
    materialized at full VARCHAR width and spill to disk (3x slower at 24 h)."""
    out: dict[str, list[int]] = {}
    for part in _window(kind):
        for row in c.execute(text(f"SELECT {key}, {_SUMS} {part} GROUP BY {key}"), w):
            acc = out.setdefault(row[0], [0] * 6)
            for i, v in enumerate(row[1:]):
                acc[i] += int(v or 0)
    return out


def triage_jobs(engine: Engine, window: int, sort: str, limit: int, now: int) -> dict:
    """Busiest jobs, targets and a time series over the last ``window`` seconds
    (at most 24 hours: the minute tables keep DETAIL_HOURS)."""
    cut = now - window
    # busiest first (row: job_id, md, read_bytes, write_bytes, ...), then by job id
    rank = {"md": lambda r: (-r[1], r[0]), "io": lambda r: (-(r[2] + r[3]), r[0])}[sort]
    step = max(60, window // 60)
    # The window starts at the minute holding ``cut`` (summaries are by
    # minute): activity that ended at or after m0. Whole hours from hstart.
    m0 = cut // MINUTE * MINUTE
    w = {"m0": m0, "hstart": -(-m0 // HOUR) * HOUR, "h0": m0 // HOUR * HOUR}
    with engine.connect() as c:
        # Grouping a day's job ids (tens of thousands) needs more than MariaDB's
        # 16 MB in-memory default; on disk it takes twice as long.
        c.execute(text(f"SET SESSION tmp_table_size = {TRIAGE_TMP}, max_heap_table_size = {TRIAGE_TMP}"))
        per_job = _window_sums(c, "job", "job_id", w)
        jobs = sorted(((k, *v) for k, v in per_job.items()), key=rank)[:limit]
        targets = sorted((k, *v) for k, v in _window_sums(c, "target", "target", w).items())
        jobs_per_target = dict(c.execute(text(
            # in by_target order: distinct jobs counted while streaming (15x faster at 24 h)
            "SELECT target, COUNT(DISTINCT job_id) FROM jobstats_presence FORCE INDEX (by_target) "
            "WHERE hour >= :h0 AND last_seen >= :m0 GROUP BY target"), w).all())
        series = c.execute(text(
            f"SELECT FLOOR(bucket / :step) * :step AS b, {_SUMS} FROM jobstats_total_minute "
            "WHERE bucket >= :m0 GROUP BY b ORDER BY b"), {**w, "step": step}).all()
        totals = c.execute(text(f"SELECT {_SUMS}, MAX(last_end) FROM jobstats_total_minute "
                                "WHERE bucket >= :m0"), w).one()
        ops: dict[str, dict[str, int]] = {}
        targets_per_job: dict[str, int] = {}
        if jobs:
            ids = {"ids": [j[0] for j in jobs]}
            expand = bindparam("ids", expanding=True)
            for part in _window("job"):
                for job_id, counts in c.execute(text(
                        f"SELECT job_id, op_counts {part} AND job_id IN :ids").bindparams(expand), {**w, **ids}):
                    merged = ops.setdefault(job_id, {})
                    for op, n in json.loads(counts or "{}").items():
                        merged[op] = merged.get(op, 0) + int(n)
            targets_per_job = dict(c.execute(text(
                "SELECT job_id, COUNT(DISTINCT target) FROM jobstats_presence "
                "WHERE hour >= :h0 AND last_seen >= :m0 AND job_id IN :ids GROUP BY job_id").bindparams(
                    bindparam("ids", expanding=True)), {**w, **ids}).all())
    return {
        "window": window, "step": step, "now": now,
        "as_of": int(totals[6]) if totals[6] is not None else None,
        "totals": _figures(totals[:6]),
        "jobs": [{"job_id": j[0], **_figures(j[1:7]), "targets": int(targets_per_job.get(j[0], 0)),
                  "top_ops": sorted(ops.get(j[0], {}).items(), key=lambda kv: -kv[1])[:4]} for j in jobs],
        "targets": [{"target": t[0], **_figures(t[1:7]), "jobs": int(jobs_per_target.get(t[0], 0))}
                    for t in targets],
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


# --- lamplighter-jobstats ------------------------------------------------------------------

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
    store = make_engine(settings.store, read_timeout=None)
    migrate(store)
    with store.connect() as lock:
        # one run at a time; released explicitly (pooled connections outlive us)
        if lock.execute(text("SELECT GET_LOCK('lamplighter_jobstats', 0)")).scalar() != 1:
            log.info("another lamplighter-jobstats run is in progress; skipping")
            return 0
        try:
            intervals, statuses = collect(settings.jobstats, store)
            n = store_intervals(store, intervals, statuses, settings.jobstats.retention_hours)
            log.info("stored %d intervals from %d sources", n, len(statuses))
            return n
        finally:
            lock.execute(text("SELECT RELEASE_LOCK('lamplighter_jobstats')"))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="lamplighter-jobstats",
                                 description="Read Lustre jobstats for Lamplighter's triage view.")
    ap.add_argument("--config", help="config file (default: $LAMPLIGHTER_WEBUI_CONFIG)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)
    try:
        run_once(load_settings(args.config))
    except Exception as exc:
        log.error("lamplighter-jobstats failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
