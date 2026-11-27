#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""lamplighter-scale: synthetic Robinhood databases and timings, for scale tests.

  lamplighter-scale gen    build a synthetic Robinhood database of any size
  lamplighter-scale bench  time the collector and the web UI's queries on it

Build a synthetic Robinhood database of any size, for scale tests.

The tables are copied from a real Robinhood database (--template), so the
schema is exactly what lamplighter creates for that configuration,
including policy status columns. Triggers are not copied: ACCT_STAT is
computed by aggregation at the end (Robinhood's triggers would fire once
per inserted row). Rows are generated inside MariaDB with INSERT ...
SELECT from its SEQUENCE engine, a million at a time; nothing is streamed
through Python. The output is deterministic for a given size and seed.

Shape (realistic enough for timing, not a model of any real site):
- a tree: /home, /projects, /scratch under the root, then 8 sub-
  directories per directory, breadth first, until --entries/--files-per-dir
  directories exist;
- files spread unevenly over directories (some directories hold many);
- sizes heavily skewed (bytes to tens of GB), ages up to six years,
  owners from --users user names (each in one of --groups groups);
- fileclass by size (small_files, big_files, outsized) and alert_status
  "alert" on outsized files, when the template has those columns.

Usage (as a MariaDB user that may create databases; never on the
production Robinhood server):
  lamplighter-scale gen --url mysql+pymysql://root:PW@host/ --template robinhood_x \\
                        --target robinhood_scale --entries 10000000
  lamplighter-scale bench --url mysql+pymysql://root:PW@host/ --db robinhood_scale \\
                        [--json results.json]
"""

from __future__ import annotations

import argparse
import re
import sys
import time

from sqlalchemy import create_engine, text

FSID = "5CA1E"
CHUNK = 1_000_000
YEAR = 365 * 86400


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def copy_schema(c, template: str, target: str) -> set[str]:
    c.execute(text(f"DROP DATABASE IF EXISTS `{target}`"))
    c.execute(text(f"CREATE DATABASE `{target}`"))
    tables = c.execute(text("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = :t "
                            "AND TABLE_TYPE = 'BASE TABLE'"), {"t": template}).scalars().all()
    for t in tables:
        ddl = c.execute(text(f"SHOW CREATE TABLE `{template}`.`{t}`")).one()[1]
        c.execute(text(f"USE `{target}`"))
        c.execute(text(ddl))
    cols = c.execute(text("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = :t "
                          "AND TABLE_NAME = 'ENTRIES'"), {"t": target}).scalars().all()
    return set(cols)


def generate(url: str, template: str, target: str, entries: int, files_per_dir: int,
             users: int, groups: int, now: int) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]+", template + target):
        sys.exit("database names: letters, digits and _ only")
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as c:
        t0 = time.monotonic()
        cols = copy_schema(c, template, target)
        c.execute(text(f"USE `{target}`"))
        c.execute(text("SET SESSION unique_checks = 0, foreign_key_checks = 0"))
        dirs = max(3, entries // files_per_dir)
        files = max(0, entries - dirs)
        log(f"{target}: {dirs:,} directories, {files:,} files (template {template})")

        # Directory i (0-based) has id FSID:(i+2); the root is FSID:1.
        # Parent: 0-2 under the root, then i -> (i-3) // 8.
        def pid(i: str) -> str:
            return (f"IF({i} < 3, '{FSID}:1', CONCAT('{FSID}:', FLOOR(({i} - 3) / 8) + 2))")

        def uid(n: str) -> str:
            return f"CONCAT('user', LPAD(({n} * 2654435761) MOD {users}, 5, '0'))"

        def gid(n: str) -> str:
            return f"CONCAT('group', LPAD((({n} * 2654435761) MOD {users}) MOD {groups}, 4, '0'))"

        extra_cols, extra_vals_dir, extra_vals_file = [], [], []
        if "fileclass" in cols:
            extra_cols.append("fileclass")
            extra_vals_dir.append("'++'")
            extra_vals_file.append("CASE WHEN sz > 20*POW(1024,3) THEN '+big_files+outsized+' "
                                   "WHEN sz > POW(1024,3) THEN '+big_files+' "
                                   "WHEN sz <= 32768 THEN '+small_files+' ELSE '++' END")
        if "alert_status" in cols:
            extra_cols.append("alert_status")
            extra_vals_dir.append("''")
            extra_vals_file.append("IF(sz > 20*POW(1024,3), 'alert', '')")
        xc = "".join(", " + x for x in extra_cols)
        xd = "".join(", " + x for x in extra_vals_dir)
        xf = "".join(", " + x for x in extra_vals_file)

        for start in range(0, dirs, CHUNK):
            n = min(CHUNK, dirs - start)
            c.execute(text(
                f"INSERT INTO ENTRIES (id, uid, gid, size, blocks, creation_time, last_access, last_mod, "
                f"last_mdchange, type, mode, nlink, md_update{xc}) "
                f"SELECT CONCAT('{FSID}:', i + 2), {uid('i')}, {gid('i')}, 4096, 8, lm - 86400, la, lm, lm, "
                f"'dir', 16877, 2, {now}{xd} FROM (SELECT seq + {start} AS i, "
                f"{now} - FLOOR(POW(RAND(seq + {start}), 2) * {6 * YEAR}) AS lm, "
                f"{now} - FLOOR(POW(RAND(seq + {start} + 7), 3) * {YEAR}) AS la "
                f"FROM seq_0_to_{n - 1}) d"))
            c.execute(text(
                f"INSERT INTO NAMES (id, pkn, parent_id, name, path_update) "
                f"SELECT id, SHA1(CONCAT(parent_id, '/', name)), parent_id, name, {now} FROM ("
                f"SELECT CONCAT('{FSID}:', i + 2) AS id, {pid('i')} AS parent_id, "
                f"ELT(LEAST(i + 1, 4), 'home', 'projects', 'scratch', CONCAT('d', i)) AS name "
                f"FROM (SELECT seq + {start} AS i FROM seq_0_to_{n - 1}) s) d"))
        log(f"directories done ({time.monotonic() - t0:.0f} s)")

        for start in range(0, files, CHUNK):
            n = min(CHUNK, files - start)
            j = f"(seq + {start})"
            c.execute(text(
                f"INSERT INTO ENTRIES (id, uid, gid, size, blocks, creation_time, last_access, last_mod, "
                f"last_mdchange, type, mode, nlink, md_update{xc}) "
                f"SELECT CONCAT('{FSID}:', {dirs + 2} + j), "
                f"IF(RAND(j + 11) < 0.9, {uid('d')}, {uid('j')}), IF(RAND(j + 11) < 0.9, {gid('d')}, {gid('j')}), "
                f"sz, CEIL(sz / 512), lm - 3600, lm + FLOOR(RAND(j + 13) * ({now} - lm)), lm, lm, 'file', 33188, 1, "
                f"{now}{xf} FROM (SELECT {j} AS j, "
                f"FLOOR(POW(RAND({j} + 17), 1.6) * {dirs}) AS d, "
                f"FLOOR(POW(10, 1 + 9.5 * POW(RAND({j} + 19), 2.5))) AS sz, "
                f"{now} - FLOOR(POW(RAND({j} + 23), 1.5) * {6 * YEAR}) AS lm "
                f"FROM seq_0_to_{n - 1}) f"))
            c.execute(text(
                f"INSERT INTO NAMES (id, pkn, parent_id, name, path_update) "
                f"SELECT id, SHA1(CONCAT(parent_id, '/', name)), parent_id, name, {now} FROM ("
                f"SELECT CONCAT('{FSID}:', {dirs + 2} + j) AS id, CONCAT('{FSID}:', d + 2) AS parent_id, "
                f"CONCAT('f', j, '.dat') AS name FROM (SELECT {j} AS j, "
                f"FLOOR(POW(RAND({j} + 17), 1.6) * {dirs}) AS d FROM seq_0_to_{n - 1}) s) f"))
            if start // CHUNK % 10 == 9 or start + n == files:
                log(f"files: {start + n:,} of {files:,} ({time.monotonic() - t0:.0f} s)")

        status = ", alert_status" if "alert_status" in cols else ""
        c.execute(text("DELETE FROM ACCT_STAT"))
        c.execute(text(
            f"INSERT INTO ACCT_STAT (uid, gid, type{status}, size, blocks, count, sz0, sz1, sz32, sz1K, sz32K, "
            f"sz1M, sz32M, sz1G, sz32G, sz1T) SELECT uid, gid, type{status}, SUM(size), SUM(blocks), COUNT(*), "
            "SUM(size = 0), SUM(size > 0 AND size < 32), SUM(size >= 32 AND size < 1024), "
            "SUM(size >= 1024 AND size < 32768), SUM(size >= 32768 AND size < 1048576), "
            "SUM(size >= 1048576 AND size < 33554432), SUM(size >= 33554432 AND size < 1073741824), "
            "SUM(size >= 1073741824 AND size < 34359738368), SUM(size >= 34359738368 AND size < 1099511627776), "
            f"SUM(size >= 1099511627776) FROM ENTRIES GROUP BY uid, gid, type{status}"))
        c.execute(text("DELETE FROM VARS"))
        for k, v in {"FS_Path": "/scale", "RootId": f"{FSID}/1", "LastScanStatus": "done",
                     "LastScanStartTime": now - 3600, "LastScanEndTime": now, "LastScanEntriesScanned": entries,
                     "LastScanErrors": 0, "LastScanTimeouts": 0, "ScanInterval": 345600}.items():
            c.execute(text("INSERT INTO VARS (varname, value) VALUES (:k, :v)"), {"k": k, "v": str(v)})
        c.execute(text("ANALYZE TABLE ENTRIES, NAMES, ACCT_STAT"))
        size = c.execute(text("SELECT SUM(DATA_LENGTH + INDEX_LENGTH) FROM information_schema.TABLES "
                              "WHERE TABLE_SCHEMA = :t"), {"t": target}).scalar()
        log(f"done: {entries:,} entries in {time.monotonic() - t0:.0f} s; {int(size) / 2**30:.1f} GiB on disk")


def _median_time(fn, runs: int = 3):
    times, out = [], None
    for _ in range(runs):
        t = time.monotonic()
        out = fn()
        times.append(time.monotonic() - t)
    return sorted(times)[len(times) // 2], out


def bench(url: str, db: str, store_db: str, rollup_depth: int, json_path: str | None) -> None:
    import json
    import types

    from .collector import LAST_TIMINGS, run_once
    from .config import CollectorConfig
    from .db import usage_by, usage_summary, user_usage
    from .search_api import Criteria, search
    from .store import age_bands, policy_counts

    base = url.rstrip("/")
    admin = create_engine(base + "/", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"CREATE DATABASE IF NOT EXISTS `{store_db}`"))
        rows = {t: int(n or 0) for t, n in c.execute(text(
            "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA = :d "
            "AND TABLE_NAME IN ('ENTRIES', 'NAMES', 'ACCT_STAT')"), {"d": db})}
        gib = float(c.execute(text("SELECT SUM(DATA_LENGTH + INDEX_LENGTH) FROM information_schema.TABLES "
                                   "WHERE TABLE_SCHEMA = :d"), {"d": db}).scalar() or 0) / 2**30
    source = create_engine(f"{base}/{db}", pool_pre_ping=True)
    store = create_engine(f"{base}/{store_db}", pool_pre_ping=True)
    results: dict = {"database": db, "entries_estimate": rows.get("ENTRIES", 0), "size_gib": round(gib, 1),
                     "collector": {}, "queries": {}}
    log(f"{db}: about {rows.get('ENTRIES', 0):,} entries, {gib:.1f} GiB; running the collector")

    settings = types.SimpleNamespace(collector=CollectorConfig(rollup_depth=rollup_depth, max_scan_seconds=86400))
    run_once(settings, source, store)
    results["collector"] = dict(LAST_TIMINGS)
    log("collector: " + ", ".join(f"{k} {v}s" for k, v in LAST_TIMINGS.items()))

    q = results["queries"]

    def record(name, fn, describe=lambda out: ""):
        t, out = _median_time(fn)
        q[name] = {"seconds": round(t, 3), "detail": describe(out)}
        log(f"{name}: {t:.3f}s {q[name]['detail']}")
        return out

    record("usage summary (ACCT_STAT)", lambda: usage_summary(source, top=10))
    users = record("users list (ACCT_STAT)", lambda: usage_by(source, "uid"), lambda o: f"{len(o)} users")
    top = max(users, key=lambda r: r["bytes"])["key"] if users else "user00000"
    record("one user's page (ACCT_STAT)", lambda: user_usage(source, top), lambda o: top)
    record("policy counts (collector output)", lambda: policy_counts(store))
    record("age histogram (collector output)", lambda: age_bands(store, "access"))
    with source.connect() as c:
        root = c.execute(text("SELECT value FROM VARS WHERE varname = 'RootId'")).scalar()
        root_id = (root or "").replace("/", ":")
        record("browse: list the root (NAMES index)", lambda: c.execute(text(
            "SELECT n.id, n.name, e.uid FROM NAMES n JOIN ENTRIES e ON e.id = n.id "
            "WHERE n.parent_id = :p LIMIT 500"), {"p": root_id}).all(), lambda o: f"{len(o)} entries")
        deep = c.execute(text(
            "SELECT n3.id FROM NAMES n1 JOIN NAMES n2 ON n2.parent_id = n1.id JOIN NAMES n3 ON n3.parent_id = n2.id "
            "JOIN ENTRIES e ON e.id = n3.id AND e.type = 'dir' WHERE n1.parent_id = :p LIMIT 1"),
            {"p": root_id}).scalar()

    def describe(o):
        return (f"{len(o['results'])} matches, {o['directories_searched']:,} directories, "
                + ("complete" if o["complete"] else f"stopped at {o['stopped']}"))

    record("search: whole tree, owner + size", lambda: search(source, store, None,
           Criteria(owner=top, min_size=2**30)), describe)
    if deep:
        record("search: subtree 3 levels down", lambda: search(source, store, deep, Criteria(type="any")), describe)

    if json_path:
        with open(json_path, "w") as fh:
            json.dump(results, fh, indent=2)
        log(f"wrote {json_path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lamplighter-scale", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="build a synthetic Robinhood database")
    g.add_argument("--url", required=True, help="SQLAlchemy URL of the MariaDB server (a user that can create databases)")
    g.add_argument("--template", required=True, help="a real Robinhood database to copy the schema from")
    g.add_argument("--target", required=True, help="database to (re)create")
    g.add_argument("--entries", type=int, required=True)
    g.add_argument("--files-per-dir", type=int, default=100)
    g.add_argument("--users", type=int, default=2000)
    g.add_argument("--groups", type=int, default=200)
    g.add_argument("--now", type=int, default=int(time.time()) // 86400 * 86400,
                   help="reference time (default: today 00:00 UTC), for reproducible ages")
    b = sub.add_parser("bench", help="time the collector and the web UI's queries")
    b.add_argument("--url", required=True, help="SQLAlchemy URL of the MariaDB server")
    b.add_argument("--db", required=True, help="the (synthetic or replica) Robinhood database")
    b.add_argument("--store-db", default="lamplighter_webui_scale", help="scratch database for collector output")
    b.add_argument("--rollup-depth", type=int, default=4)
    b.add_argument("--json", help="also write the results to this file")
    a = ap.parse_args(argv)
    if a.cmd == "gen":
        generate(a.url, a.template, a.target, a.entries, a.files_per_dir, a.users, a.groups, a.now)
    else:
        bench(a.url, a.db, a.store_db, a.rollup_depth, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
