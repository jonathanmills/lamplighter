# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""lamplighter-collector: periodic summaries of Robinhood's database.

Run from a timer (nightly), never from a web request. One run:

1. Age histogram: ONE scan of Robinhood's ENTRIES table, aggregated by
   MariaDB itself (GROUP BY owner, group and age bands), so only a few
   thousand summary rows ever leave the database, whatever the size of
   the filesystem. Point ``database`` at a replica to keep the scan off
   the server Robinhood writes to.
2. A copy of ACCT_STAT (cheap; maintained by Robinhood's triggers),
   which gives usage history over time.
3. A run record, and deletion of runs older than ``keep_runs``.

Usage: lamplighter-collector [run|migrate] [--config FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .config import Settings, load_settings
from .db import make_engine
from .policies import collect_counts_parallel
from .rollup import compute as compute_rollups
from .store import migrate

log = logging.getLogger("lamplighter_webui.collector")
# Seconds per pass of the newest run in this process (for lamplighter-scale bench).
LAST_TIMINGS: dict[str, float] = {}

DAY = 86400
# (band number, label, upper age limit in seconds; None = no limit).
AGE_BANDS: list[tuple[int, str, int | None]] = [
    (0, "< 7 days", 7 * DAY),
    (1, "7–30 days", 30 * DAY),
    (2, "30–90 days", 90 * DAY),
    (3, "90 days – 1 year", 365 * DAY),
    (4, "1–3 years", 3 * 365 * DAY),
    (5, "> 3 years", None),
]


def _band_sql(column: str) -> str:
    """CASE expression mapping an epoch column to its age band.

    Missing or zero times fall in the oldest band (unknown counts as cold).
    """
    whens = " ".join(f"WHEN {column} >= :t{n} THEN {n}" for n, _, lim in AGE_BANDS if lim)
    return f"CASE {whens} ELSE {AGE_BANDS[-1][0]} END"


AGE_QUERY = (
    "SELECT uid, gid, {a} AS access_band, {m} AS modify_band, "
    "COUNT(*) AS files, COALESCE(SUM(size), 0) AS bytes "
    "FROM ENTRIES WHERE type = 'file' AND {{range}} "
    "GROUP BY uid, gid, access_band, modify_band"
).format(a=_band_sql("last_access"), m=_band_sql("last_mod"))


def _s(v) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)


def _age_histogram(source: Engine, now: int, workers: int, setup) -> list[dict]:
    """One scan of ENTRIES, split by primary-key range across workers
    (parallel.py); partial counts and sums add up exactly."""
    from .parallel import map_ranges, where
    params = {f"t{n}": now - lim for n, _, lim in AGE_BANDS if lim}
    tmp = "lamplighter_age_acc"

    def start(c):
        # Each connection folds its chunks into this table on the server and
        # returns it once (see parallel.map_ranges).
        if setup:
            setup(c)
        c.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {tmp}"))
        c.execute(text(f"CREATE TEMPORARY TABLE {tmp} (uid VARBINARY(127), gid VARBINARY(127), ab TINYINT, "
                       "mb TINYINT, files BIGINT UNSIGNED, bytes BIGINT UNSIGNED, UNIQUE KEY (uid, gid, ab, mb)) "
                       "ENGINE=MEMORY"))

    def scan(c, r):
        cond, rp = where(r)
        c.execute(text(f"INSERT INTO {tmp} (uid, gid, ab, mb, files, bytes) " + AGE_QUERY.format(range=cond)
                       + " ON DUPLICATE KEY UPDATE files = files + VALUES(files), bytes = bytes + VALUES(bytes)"),
                  {**params, **rp})

    def finish(c):
        rows = c.execute(text(f"SELECT uid, gid, ab, mb, files, bytes FROM {tmp}")).all()
        c.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {tmp}"))
        return rows

    acc: dict[tuple, list[int]] = {}
    for part in map_ranges(source, workers, scan, start, finish):
        for u, g, a, m, f, b in part:
            cell = acc.setdefault((_s(u), _s(g), int(a), int(m)), [0, 0])
            cell[0] += int(f)
            cell[1] += int(b)
    return [{"uid": u, "gid": g, "ab": a, "mb": m, "files": f, "bytes": b}
            for (u, g, a, m), (f, b) in acc.items()]


def _acct_snapshot(src: Connection) -> list[dict]:
    # Summed: once a site defines policies with status managers, Robinhood
    # adds their status columns to ACCT_STAT's key (e.g. alert_status), so
    # one owner, group and type can span several rows.
    rows = src.execute(text("SELECT uid, gid, type, SUM(count), SUM(size), SUM(blocks) FROM ACCT_STAT "
                            "GROUP BY uid, gid, type")).all()
    return [{"uid": _s(u), "gid": _s(g), "type": _s(t), "n": int(n or 0),
             "b": int(b or 0), "bl": int(bl or 0)} for u, g, t, n, b, bl in rows]


def run_once(settings: Settings, source: Engine | None = None, store: Engine | None = None) -> int:
    """One collector run. Returns the run id. Raises on failure."""
    # No client read timeout: scans run for minutes or hours at scale
    # (max_scan_seconds bounds them on the server). One connection per worker.
    workers = max(1, settings.collector.workers)
    source = source or make_engine(settings.database, read_timeout=None, pool_size=workers + 1)
    store = store or make_engine(settings.store, read_timeout=None)
    migrate(store)

    with store.connect() as lock_conn:
        # One collector at a time. GET_LOCK belongs to this connection, and a
        # pooled connection stays open after use, so release it explicitly.
        if not lock_conn.execute(text("SELECT GET_LOCK('lamplighter_collector', 0)")).scalar():
            raise RuntimeError("another collector run is in progress")
        try:
            return _run_locked(settings, source, store)
        finally:
            lock_conn.execute(text("SELECT RELEASE_LOCK('lamplighter_collector')"))


def _run_locked(settings: Settings, source: Engine, store: Engine) -> int:
    """The body of one run, while holding the collector lock."""
    with store.begin() as c:
        run_id = c.execute(text("INSERT INTO collector_runs (started_at, status) "
                                "VALUES (NOW(3), 'running')")).lastrowid
    t0 = time.monotonic()
    try:
        now = int(time.time())
        timings: dict[str, float] = {}

        def timed(name, fn, *args):
            t = time.monotonic()
            out = fn(*args)
            timings[name] = round(time.monotonic() - t, 2)
            return out

        cfg = settings.collector

        def setup(c: Connection) -> None:
            # Abort a runaway scan rather than load the server indefinitely,
            # and group in memory (the 16 MB default spills to disk).
            c.execute(text(f"SET SESSION max_statement_time = {int(cfg.max_scan_seconds)}, "
                           f"tmp_table_size = {int(cfg.work_mem)}, max_heap_table_size = {int(cfg.work_mem)}"))

        workers = max(1, cfg.workers)
        hist = timed("age", _age_histogram, source, now, workers, setup)
        classes = (timed("classes", collect_counts_parallel, source, workers, setup) if cfg.policy_counts else [])
        with source.connect() as src:
            setup(src)
            acct = timed("acct", _acct_snapshot, src)
            fs_path = src.execute(text("SELECT value FROM VARS WHERE varname = 'FS_Path'")).scalar()
            dirs = timed("rollup", compute_rollups, src, now, cfg.rollup_depth, _s(fs_path or "/"), cfg.work_mem)
        t_store = time.monotonic()
        files = sum(r["files"] for r in hist)
        with store.begin() as c:
            if hist:
                c.execute(text(
                    "INSERT INTO age_hist (run_id, uid, gid, access_band, modify_band, files, bytes) "
                    f"VALUES ({run_id}, :uid, :gid, :ab, :mb, :files, :bytes)"), hist)
            if acct:
                c.execute(text(
                    "INSERT INTO acct_history (run_id, uid, gid, type, entries, bytes, blocks) "
                    f"VALUES ({run_id}, :uid, :gid, :type, :n, :b, :bl)"), acct)
            if dirs:
                c.execute(text(
                    "INSERT INTO dir_rollup (run_id, dir_id, parent_dir, depth, path, owner, top_owner, "
                    "top_owner_bytes, subdirs, files, bytes, newest_access, oldest_access, cold_1y_bytes, "
                    f"cold_3y_bytes) VALUES ({run_id}, :dir_id, :parent_dir, :depth, :path, :owner, "
                    ":top_owner, :top_owner_bytes, :subdirs, :files, :bytes, :newest_access, "
                    ":oldest_access, :cold_1y_bytes, :cold_3y_bytes)"), dirs)
            if classes:
                c.execute(text(
                    "INSERT INTO policy_counts (run_id, fileclass, status_field, status_value, entries, bytes) "
                    f"VALUES ({run_id}, :fileclass, :field, :value, :entries, :bytes)"), classes)
            c.execute(text("UPDATE collector_runs SET status = 'ok', finished_at = NOW(3), "
                           "entries = :n WHERE id = :id"), {"n": files, "id": run_id})
        timings["store"] = round(time.monotonic() - t_store, 2)
        LAST_TIMINGS.clear()
        LAST_TIMINGS.update(timings, total=round(time.monotonic() - t0, 2))
        log.info("run %d ok: %d files in %d age rows, %d acct rows, %d directories, "
                 "%d fileclass/status rows, %.1fs (%s)",
                 run_id, files, len(hist), len(acct), len(dirs), len(classes), time.monotonic() - t0,
                 ", ".join(f"{k} {v}s" for k, v in timings.items()))
    except Exception as exc:
        with store.begin() as c:
            c.execute(text("UPDATE collector_runs SET status = 'failed', finished_at = NOW(3), "
                           "error = :e WHERE id = :id"), {"e": f"{type(exc).__name__}: {exc}"[:4000], "id": run_id})
        raise

    # Retention: keep the newest keep_runs runs (rows go with them: ON DELETE CASCADE),
    # and drop database copies of audit events past their retention.
    with store.begin() as c:
        c.execute(text("DELETE FROM audit_log WHERE ts < UTC_TIMESTAMP() - INTERVAL :d DAY"),
                  {"d": settings.collector.audit_retention_days})
        c.execute(text(
            "DELETE FROM collector_runs WHERE id < (SELECT MIN(id) FROM "
            "(SELECT id FROM collector_runs ORDER BY id DESC LIMIT :k) AS keep)"),
            {"k": settings.collector.keep_runs})
    return run_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lamplighter-collector", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="run", choices=["run", "migrate"])
    ap.add_argument("--config", help="config file (default: $LAMPLIGHTER_WEBUI_CONFIG)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings(args.config)
    try:
        if args.command == "migrate":
            log.info("schema version %d", migrate(make_engine(settings.store)))
        else:
            run_once(settings)
    except Exception as exc:
        log.error("collector failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
