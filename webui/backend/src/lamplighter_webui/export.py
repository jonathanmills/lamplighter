# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""General data export: any table the web UI shows, as CSV or JSON.

Endpoints that return a table take ``?format=csv`` or ``?format=json``
(and ``&table=`` when a page has more than one) and answer with
``export()``: the same rows the page shows, as a download.

Rule: whoever may see a table may export it, except lists of paths or of
other people's e-mail addresses, which also need ``cold.export`` (as the
search and cold-owner exports always have). Exporting one's own files
needs nothing more. Every export is audited.
"""

from __future__ import annotations

import csv
import io
import json
import time
from contextvars import ContextVar
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import Response

from .acl import Perm
from .audit import audit
from .auth import Principal

Format = Literal["csv", "json"]

# A cell starting with one of these would be a formula in a spreadsheet
# (CSV injection); such cells get a leading apostrophe.
_FORMULA = ("=", "+", "-", "@", "\t", "\r")

# Set by the report runner, which audits its own "report.run" events.
QUIET: ContextVar[bool] = ContextVar("lamplighter_export_quiet", default=False)


def require_export(p: Principal, reason: str) -> None:
    """Exports of paths or other people's contact details need cold.export."""
    if not p.grants.allows(Perm.COLD_EXPORT):
        audit("denied", user=p.user.uid, outcome="denied", permission=Perm.COLD_EXPORT.value, reason=reason)
        raise HTTPException(403, f"Exporting {reason} needs the cold.export permission.")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple, dict)):
        v = json.dumps(v, sort_keys=True, default=str)
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", "replace")
    if isinstance(v, str) and v.startswith(_FORMULA):
        return "'" + v
    return v


def to_csv(columns: list[str], rows: list[dict]) -> str:
    """Rows as formula-safe CSV text, with a header line."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in columns])
    return buf.getvalue()


def export(request: Request, p: Principal, name: str, rows: list[dict], fmt: Format,
           columns: list[str] | None = None, **audit_fields) -> Response:
    """``rows`` as a CSV or JSON download named lamplighter-<name>-<time>.<fmt>.
    ``columns`` fixes the columns and their order (default: every key, in
    first-seen order)."""
    if columns is None:
        columns = list(dict.fromkeys(k for r in rows for k in r))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    filename = f"lamplighter-{name}-{stamp}.{fmt}"
    if not QUIET.get():
        audit("export", user=p.user.uid, client=request.client.host if request.client else None,
              table=name, format=fmt, rows=len(rows), **audit_fields)
    if fmt == "json":
        body = json.dumps({"table": name, "exported_at": int(time.time()), "columns": columns,
                           "rows": [{c: r.get(c) for c in columns} for r in rows]}, default=str)
        media = "application/json"
    else:
        body, media = to_csv(columns, rows), "text/csv; charset=utf-8"
    return Response(body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
