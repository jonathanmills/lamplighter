# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Read-only access to Robinhood's MariaDB schema.

Only cheap reads: the VARS table and ACCT_STAT (maintained by
Robinhood's triggers). Nothing here may scan ENTRIES; that is the
collector's job (doc/webui-design.md, section 2).
"""

from __future__ import annotations

import time
from urllib.parse import quote_plus

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Engine

from .config import DatabaseConfig, read_secret

# VARS keys reported by the monitoring endpoint, when present. The
# changelog counters exist only with Lustre changelogs enabled.
STATUS_VARS = [
    "FS_Path", "LastScanStatus", "LastScanStartTime", "LastScanEndTime",
    "LastScanEntriesScanned", "LastScanErrors", "LastScanTimeouts",
    "ScanInterval", "CL_LastRead", "CL_LastPushed", "CL_LastCommit",
    "CL_LastCleared", "ChangelogLastRecTime",
]


def _s(value) -> str:
    """Robinhood stores some text in binary columns: decode them."""
    return value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)


def make_engine(cfg: DatabaseConfig, read_timeout: int | None = 10, pool_size: int = 5) -> Engine:
    """An engine for one database.

    Web requests keep the default 10 s read timeout: no page may wait
    longer. Batch jobs (collector, jobstats) pass read_timeout=None: their
    queries run for minutes or hours at scale and are bounded by MariaDB's
    max_statement_time instead.
    """
    url = (f"mysql+pymysql://{quote_plus(cfg.user)}:{quote_plus(read_secret(cfg.password_file))}"
           f"@{cfg.host}:{cfg.port}/{cfg.name}?charset=utf8mb4")
    args = {"write_timeout": 60} if read_timeout is None else {"read_timeout": read_timeout, "write_timeout": 10}
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600, pool_size=pool_size, connect_args=args)


def robinhood_status(engine: Engine) -> dict:
    query = text("SELECT varname, value FROM VARS WHERE varname IN :names").bindparams(
        bindparam("names", expanding=True))
    with engine.connect() as c:
        rows = c.execute(query, {"names": STATUS_VARS}).all()
    v = {_s(k): _s(val) for k, val in rows}
    out: dict = {"filesystem": v.get("FS_Path"), "now": int(time.time())}
    scan: dict = {"status": v.get("LastScanStatus")}
    for key, name in (("LastScanStartTime", "started"), ("LastScanEndTime", "ended")):
        if v.get(key, "").isdigit():
            scan[name] = int(v[key])
    if "ended" in scan:
        scan["age_seconds"] = out["now"] - scan["ended"]
    for key, name in (("LastScanEntriesScanned", "entries"), ("LastScanErrors", "errors"),
                      ("LastScanTimeouts", "timeouts"), ("ScanInterval", "interval_seconds")):
        if v.get(key, "").isdigit():
            scan[name] = int(v[key])
    out["last_scan"] = scan
    cl = {name: int(v[key]) for key, name in (
        ("CL_LastRead", "last_read"), ("CL_LastPushed", "last_pushed"),
        ("CL_LastCommit", "last_committed"), ("CL_LastCleared", "last_cleared"))
        if v.get(key, "").isdigit()}
    if cl:
        if "last_read" in cl and "last_cleared" in cl:
            cl["backlog_records"] = cl["last_read"] - cl["last_cleared"]
        out["changelog"] = cl
    return out


def usage_summary(engine: Engine, top: int = 10) -> dict:
    with engine.connect() as c:
        totals = c.execute(text(
            "SELECT type, SUM(count) AS n, SUM(size) AS bytes FROM ACCT_STAT GROUP BY type")).all()
        sizes = c.execute(text(
            "SELECT SUM(sz0+sz1+sz32), SUM(sz1K+sz32K), SUM(sz1M+sz32M), "
            "SUM(sz1G+sz32G+sz1T) FROM ACCT_STAT WHERE type = 'file'")).one()
        users = c.execute(text(
            "SELECT uid, SUM(count) AS n, SUM(size) AS bytes FROM ACCT_STAT "
            "GROUP BY uid ORDER BY bytes DESC LIMIT :top"), {"top": top}).all()
    return {
        "by_type": {t: {"entries": int(n or 0), "bytes": int(b or 0)} for t, n, b in totals},
        "top_users": [{"user": _s(u), "entries": int(n or 0), "bytes": int(b or 0)}
                      for u, n, b in users],
        # File counts by size, from Robinhood's size profile (ACCT_STAT sz* columns).
        "file_sizes": [{"bucket": b, "files": int(n or 0)} for b, n in
                       zip(("< 1 KiB", "1 KiB – 1 MiB", "1 MiB – 1 GiB", "≥ 1 GiB"), sizes)],
    }


# --- per-owner and per-group usage (ACCT_STAT: cheap) --------------------

_SMALL = "sz0 + sz1 + sz32 + sz1K"        # files under 32 KiB
_OWNER_COLS = (
    "SUM(CASE WHEN type = 'file' THEN count ELSE 0 END) AS files, "
    "SUM(CASE WHEN type = 'dir' THEN count ELSE 0 END) AS dirs, "
    "SUM(count) AS entries, COALESCE(SUM(size), 0) AS bytes, "
    f"SUM(CASE WHEN type = 'file' THEN {_SMALL} ELSE 0 END) AS small_files"
)


def _owner_rows(rows) -> list[dict]:
    return [{"key": _s(k), "files": int(f or 0), "dirs": int(d or 0), "entries": int(e or 0),
             "bytes": int(b or 0), "small_files": int(sm or 0)} for k, f, d, e, b, sm in rows]


def usage_by(engine: Engine, key: str) -> list[dict]:
    """Totals per user ("uid") or per group ("gid")."""
    col = {"uid": "uid", "gid": "gid"}[key]
    extra = ", COUNT(DISTINCT uid) AS users" if col == "gid" else ""
    with engine.connect() as c:
        rows = c.execute(text(f"SELECT {col}, {_OWNER_COLS}{extra} FROM ACCT_STAT GROUP BY {col}")).all()
    out = _owner_rows([r[:6] for r in rows])
    if col == "gid":
        for o, r in zip(out, rows):
            o["users"] = int(r[6])
    return out


def user_usage(engine: Engine, uid: str) -> dict | None:
    with engine.connect() as c:
        tot = c.execute(text(f"SELECT {_OWNER_COLS} FROM ACCT_STAT WHERE uid = :u"), {"u": uid}).one()
        if not tot[2]:
            return None
        sizes = c.execute(text(
            "SELECT SUM(sz0+sz1+sz32), SUM(sz1K+sz32K), SUM(sz1M+sz32M), SUM(sz1G+sz32G+sz1T) "
            "FROM ACCT_STAT WHERE uid = :u AND type = 'file'"), {"u": uid}).one()
        groups = c.execute(text(
            "SELECT gid, SUM(count), COALESCE(SUM(size), 0) FROM ACCT_STAT WHERE uid = :u "
            "GROUP BY gid ORDER BY 3 DESC"), {"u": uid}).all()
    f, d, e, b, sm = (int(x or 0) for x in tot)
    return {
        "files": f, "dirs": d, "entries": e, "bytes": b, "small_files": sm,
        "file_sizes": [{"bucket": k, "files": int(n or 0)} for k, n in
                       zip(("< 1 KiB", "1 KiB – 1 MiB", "1 MiB – 1 GiB", "≥ 1 GiB"), sizes)],
        "groups": [{"group": _s(g), "entries": int(n or 0), "bytes": int(by or 0)} for g, n, by in groups],
    }


def bulk_insert(c, table: str, cols: tuple[str, ...], rows, chunk: int = 10_000) -> int:
    """Insert many rows through the driver's multi-row INSERT, on the
    connection (and transaction) ``c``. About 8x faster than SQLAlchemy's
    executemany with named parameters for hundreds of thousands of rows.
    ``rows`` are tuples in ``cols`` order; ``table`` and ``cols`` are trusted
    identifiers from our own code, never user input."""
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    cur = c.connection.cursor()
    n = 0
    try:
        for i in range(0, len(rows), chunk):
            cur.executemany(sql, rows[i:i + chunk])
            n += len(rows[i:i + chunk])
    finally:
        cur.close()
    return n

