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


def make_engine(cfg: DatabaseConfig) -> Engine:
    url = (f"mysql+pymysql://{quote_plus(cfg.user)}:{quote_plus(read_secret(cfg.password_file))}"
           f"@{cfg.host}:{cfg.port}/{cfg.name}?charset=utf8mb4")
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600,
                         connect_args={"read_timeout": 10, "write_timeout": 10})


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
