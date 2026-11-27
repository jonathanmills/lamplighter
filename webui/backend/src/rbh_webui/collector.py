# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""rbh-webui-collector: periodic summaries of Robinhood's database.

Run from a timer (nightly), never from a web request. One run:

1. Age histogram: ONE scan of Robinhood's ENTRIES table, aggregated by
   MariaDB itself (GROUP BY owner, group and age bands), so only a few
   thousand summary rows ever leave the database, whatever the size of
   the filesystem. Point ``database`` at a replica to keep the scan off
   the server Robinhood writes to.
2. A copy of ACCT_STAT (cheap; maintained by Robinhood's triggers),
   which gives usage history over time.
3. A run record, and deletion of runs older than ``keep_runs``.

Usage: rbh-webui-collector [run|migrate] [--config FILE]
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
from .rollup import compute as compute_rollups
from .store import migrate

log = logging.getLogger("rbh_webui.collector")

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
    "FROM ENTRIES WHERE type = 'file' "
    "GROUP BY uid, gid, access_band, modify_band"
).format(a=_band_sql("last_access"), m=_band_sql("last_mod"))


def _s(v) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)


def _age_histogram(src: Connection, now: int, max_seconds: int) -> list[dict]:
    # Abort a runaway scan rather than load the server indefinitely.
    src.execute(text(f"SET SESSION max_statement_time = {int(max_seconds)}"))
    params = {f"t{n}": now - lim for n, _, lim in AGE_BANDS if lim}
    rows = src.execute(text(AGE_QUERY), params).all()
    return [{"uid": _s(u), "gid": _s(g), "ab": int(a), "mb": int(m),
             "files": int(f), "bytes": int(b)} for u, g, a, m, f, b in rows]


def _acct_snapshot(src: Connection) -> list[dict]:
    rows = src.execute(text("SELECT uid, gid, type, count, size, blocks FROM ACCT_STAT")).all()
    return [{"uid": _s(u), "gid": _s(g), "type": _s(t), "n": int(n or 0),
             "b": int(b or 0), "bl": int(bl or 0)} for u, g, t, n, b, bl in rows]


def run_once(settings: Settings, source: Engine | None = None, store: Engine | None = None) -> int:
    """One collector run. Returns the run id. Raises on failure."""
    source = source or make_engine(settings.database)
    store = store or make_engine(settings.store)
    migrate(store)

    with store.connect() as lock_conn:
        # One collector at a time. GET_LOCK belongs to this connection, and a
        # pooled connection stays open after use, so release it explicitly.
        if not lock_conn.execute(text("SELECT GET_LOCK('rbh_webui_collector', 0)")).scalar():
            raise RuntimeError("another collector run is in progress")
        try:
            return _run_locked(settings, source, store)
        finally:
            lock_conn.execute(text("SELECT RELEASE_LOCK('rbh_webui_collector')"))


def _run_locked(settings: Settings, source: Engine, store: Engine) -> int:
    """The body of one run, while holding the collector lock."""
    with store.begin() as c:
        run_id = c.execute(text("INSERT INTO collector_runs (started_at, status) "
                                "VALUES (NOW(3), 'running')")).lastrowid
    t0 = time.monotonic()
    try:
        now = int(time.time())
        with source.connect() as src:
            hist = _age_histogram(src, now, settings.collector.max_scan_seconds)
            acct = _acct_snapshot(src)
            fs_path = src.execute(text("SELECT value FROM VARS WHERE varname = 'FS_Path'")).scalar()
            dirs = compute_rollups(src, now, settings.collector.rollup_depth, _s(fs_path or "/"))
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
            c.execute(text("UPDATE collector_runs SET status = 'ok', finished_at = NOW(3), "
                           "entries = :n WHERE id = :id"), {"n": files, "id": run_id})
        log.info("run %d ok: %d files in %d age rows, %d acct rows, %d directories, %.1fs",
                 run_id, files, len(hist), len(acct), len(dirs), time.monotonic() - t0)
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
    ap = argparse.ArgumentParser(prog="rbh-webui-collector", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="run", choices=["run", "migrate"])
    ap.add_argument("--config", help="config file (default: $RBH_WEBUI_CONFIG)")
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
