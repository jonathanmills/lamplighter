# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""The web UI's own schema (rbh_webui): migrations and reads.

Migrations are numbered SQL files in rbh_webui/migrations, applied in
order and recorded in schema_version. They are never edited once
released; changes go in a new file.
"""

from __future__ import annotations

import logging
import re
from importlib import resources

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


def _migrations() -> list[tuple[int, str, str]]:
    found = []
    for f in resources.files("rbh_webui.migrations").iterdir():
        m = re.match(r"(\d+)_.*\.sql$", f.name)
        if m:
            found.append((int(m.group(1)), f.name, f.read_text(encoding="utf-8")))
    return sorted(found)


def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def migrate(engine: Engine) -> int:
    """Apply pending migrations. Returns the schema version afterwards."""
    with engine.begin() as c:
        c.execute(text("CREATE TABLE IF NOT EXISTS schema_version ("
                       "version INT NOT NULL PRIMARY KEY, name VARCHAR(255) NOT NULL, "
                       "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB"))
        # Serialize concurrent migrators (e.g. two collectors starting at once).
        c.execute(text("SELECT GET_LOCK('rbh_webui_migrate', 60)"))
        try:
            done = {v for (v,) in c.execute(text("SELECT version FROM schema_version"))}
            for version, name, sql in _migrations():
                if version in done:
                    continue
                log.info("applying migration %s", name)
                for stmt in _statements(sql):
                    c.execute(text(stmt))
                c.execute(text("INSERT INTO schema_version (version, name) VALUES (:v, :n)"),
                          {"v": version, "n": name})
            return c.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_version")).scalar_one()
        finally:
            c.execute(text("SELECT RELEASE_LOCK('rbh_webui_migrate')"))


# --- reads for the API ----------------------------------------------------

def collector_runs(engine: Engine, limit: int = 10) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT id, started_at, finished_at, status, entries, error, "
            "UNIX_TIMESTAMP(started_at), UNIX_TIMESTAMP(finished_at) "
            "FROM collector_runs ORDER BY id DESC LIMIT :n"), {"n": limit}).all()
    return [{"id": r[0], "status": r[3], "entries": r[4], "error": r[5],
             "started": int(r[6]) if r[6] is not None else None,
             "finished": int(r[7]) if r[7] is not None else None} for r in rows]


def _latest_ok(c) -> tuple[int, int] | None:
    row = c.execute(text("SELECT id, UNIX_TIMESTAMP(finished_at) FROM collector_runs "
                         "WHERE status = 'ok' ORDER BY id DESC LIMIT 1")).first()
    return (int(row[0]), int(row[1])) if row else None


def age_bands(engine: Engine, basis: str) -> dict | None:
    """Files and bytes per age band, from the newest successful run."""
    from .collector import AGE_BANDS
    col = {"access": "access_band", "modify": "modify_band"}[basis]
    with engine.connect() as c:
        latest = _latest_ok(c)
        if latest is None:
            return None
        rows = dict((int(b), (int(f), int(by))) for b, f, by in c.execute(text(
            f"SELECT {col}, SUM(files), SUM(bytes) FROM age_hist WHERE run_id = :r GROUP BY {col}"),
            {"r": latest[0]}))
    return {"run_id": latest[0], "as_of": latest[1], "basis": basis,
            "bands": [{"band": n, "label": label, "files": rows.get(n, (0, 0))[0],
                       "bytes": rows.get(n, (0, 0))[1]} for n, label, _ in AGE_BANDS]}


def cold_owners(engine: Engine, min_band: int, limit: int = 25) -> dict | None:
    """Owners ranked by bytes not accessed for at least band ``min_band``."""
    with engine.connect() as c:
        latest = _latest_ok(c)
        if latest is None:
            return None
        rows = c.execute(text(
            "SELECT uid, SUM(CASE WHEN access_band >= :mb THEN bytes ELSE 0 END) AS cold_bytes, "
            "SUM(CASE WHEN access_band >= :mb THEN files ELSE 0 END) AS cold_files, "
            "SUM(bytes) AS total_bytes, MAX(access_band) AS oldest_band "
            "FROM age_hist WHERE run_id = :r GROUP BY uid HAVING cold_bytes > 0 "
            "ORDER BY cold_bytes DESC LIMIT :n"), {"r": latest[0], "mb": min_band, "n": limit}).all()
    return {"run_id": latest[0], "as_of": latest[1], "min_band": min_band,
            "owners": [{"user": u, "cold_bytes": int(cb), "cold_files": int(cf),
                        "total_bytes": int(tb), "oldest_band": int(ob)} for u, cb, cf, tb, ob in rows]}


def snapshot_bytes(engine: Engine, key: str) -> tuple[int | None, dict[str, int]]:
    """Bytes per uid or gid in the newest successful run's ACCT_STAT copy."""
    col = {"uid": "uid", "gid": "gid"}[key]
    with engine.connect() as c:
        latest = _latest_ok(c)
        if latest is None:
            return None, {}
        rows = c.execute(text(f"SELECT {col}, SUM(bytes) FROM acct_history WHERE run_id = :r "
                              f"GROUP BY {col}"), {"r": latest[0]}).all()
    return latest[1], {k: int(b) for k, b in rows}


def user_history(engine: Engine, uid: str) -> list[dict]:
    """Bytes and entries per successful run, oldest first."""
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT UNIX_TIMESTAMP(r.finished_at), SUM(h.bytes), SUM(h.entries) "
            "FROM collector_runs r JOIN acct_history h ON h.run_id = r.id "
            "WHERE r.status = 'ok' AND h.uid = :u GROUP BY r.id ORDER BY r.id"), {"u": uid}).all()
    return [{"at": int(t), "bytes": int(b), "entries": int(e)} for t, b, e in rows]


def user_age_bands(engine: Engine, uid: str) -> dict | None:
    from .collector import AGE_BANDS
    with engine.connect() as c:
        latest = _latest_ok(c)
        if latest is None:
            return None
        rows = dict((int(b), (int(f), int(by))) for b, f, by in c.execute(text(
            "SELECT access_band, SUM(files), SUM(bytes) FROM age_hist "
            "WHERE run_id = :r AND uid = :u GROUP BY access_band"), {"r": latest[0], "u": uid}))
    return {"as_of": latest[1], "bands": [
        {"band": n, "label": label, "files": rows.get(n, (0, 0))[0], "bytes": rows.get(n, (0, 0))[1]}
        for n, label, _ in AGE_BANDS]}
