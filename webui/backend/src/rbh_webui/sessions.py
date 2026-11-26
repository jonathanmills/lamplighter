# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Server-side login sessions.

The browser's cookie holds only a random session id (inside Starlette's
signed session cookie). Everything else, including the OIDC ID token,
stays on the server, so tokens never reach the browser.

This store is in memory: sessions end when the service restarts, and
it serves a single process. A shared store (database or Redis) is a
later step, needed before running several workers.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


@dataclass
class Session:
    sid: str
    uid: str
    method: str                 # "oidc" or "ldap"
    created: float
    expires: float
    id_token: str | None = None  # for RP-initiated logout (OIDC only)


class SessionStore:
    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._data: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, uid: str, method: str, id_token: str | None = None) -> Session:
        now = time.time()
        s = Session(sid=secrets.token_urlsafe(32), uid=uid, method=method,
                    created=now, expires=now + self.ttl, id_token=id_token)
        with self._lock:
            self._purge(now)
            self._data[s.sid] = s
        return s

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        with self._lock:
            s = self._data.get(sid)
            if s and s.expires <= time.time():
                del self._data[sid]
                return None
            return s

    def delete(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        with self._lock:
            return self._data.pop(sid, None)

    def _purge(self, now: float) -> None:
        for sid in [k for k, v in self._data.items() if v.expires <= now]:
            del self._data[sid]
