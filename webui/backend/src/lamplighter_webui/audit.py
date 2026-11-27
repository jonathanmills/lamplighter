# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Audit log.

Every event is written as one JSON line on the "lamplighter_webui.audit" logger
(journald under systemd: the authoritative copy for the site's log
collection). When a store is attached, events are also queued to the
lamplighter_webui.audit_log table, so admins can search them in the UI. A
background thread does the inserts, so a request never waits on the
database, and a database outage never blocks or fails a request.

Never log secrets, passwords or tokens.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

_log = logging.getLogger("lamplighter_webui.audit")
_queue: "queue.Queue[dict]" = queue.Queue(maxsize=10000)
_writer: threading.Thread | None = None


def audit(event: str, *, user: str | None = None, outcome: str = "ok",
          client: str | None = None, **details) -> None:
    record = {"ts": round(time.time(), 3), "event": event, "outcome": outcome,
              "user": user, "client": client}
    record.update(details)
    _log.info(json.dumps(record, sort_keys=True, default=str))
    if _writer is not None:
        try:
            _queue.put_nowait(record)
        except queue.Full:
            _log.warning("audit queue full: event kept in the log only")


def _write_loop(engine: Engine) -> None:
    while True:
        batch = [_queue.get()]
        while len(batch) < 200:
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break
        rows = []
        for r in batch:
            extra = {k: v for k, v in r.items() if k not in ("ts", "event", "outcome", "user", "client")}
            rows.append({"ts": datetime.fromtimestamp(r["ts"], tz=timezone.utc).replace(tzinfo=None),
                         "event": r["event"][:64], "outcome": str(r["outcome"])[:16],
                         "user": r["user"], "client": r["client"],
                         "details": json.dumps(extra, sort_keys=True, default=str) if extra else None})
        try:
            with engine.begin() as c:
                c.execute(text("INSERT INTO audit_log (ts, event, outcome, user, client, details) "
                               "VALUES (:ts, :event, :outcome, :user, :client, :details)"), rows)
        except Exception as exc:  # never let the writer die
            _log.warning("audit events not stored in the database (%d kept in the log): %s",
                         len(rows), exc)
        finally:
            for _ in batch:
                _queue.task_done()


def attach_store(engine: Engine) -> None:
    """Start copying audit events to the database (idempotent)."""
    global _writer
    if _writer is None:
        _writer = threading.Thread(target=_write_loop, args=(engine,), name="audit-writer", daemon=True)
        _writer.start()


def flush(timeout: float = 5.0) -> None:
    """Wait until queued events are stored (tests, shutdown)."""
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.02)
