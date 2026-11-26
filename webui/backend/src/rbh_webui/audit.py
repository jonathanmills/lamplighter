# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Audit log: one JSON object per line on the "rbh_webui.audit" logger.

Under systemd this goes to journald; route it to the site's log
collection from there. Never log secrets, passwords or tokens.
"""

from __future__ import annotations

import json
import logging
import time

_log = logging.getLogger("rbh_webui.audit")


def audit(event: str, *, user: str | None = None, outcome: str = "ok",
          client: str | None = None, **details) -> None:
    record = {"ts": round(time.time(), 3), "event": event, "outcome": outcome,
              "user": user, "client": client}
    record.update(details)
    _log.info(json.dumps(record, sort_keys=True, default=str))
