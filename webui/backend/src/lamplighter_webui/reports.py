# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Scheduled reports: the equivalent of Robinhood's PHP GUI "tasks" and
custom graphs (gui_v3), for feature parity.

A report is one of the web UI's exportable tables (a *source*: users,
cold owners, a saved search...) with saved parameters, a schedule and a
number of runs to keep (1: the latest only; more: a history that custom
graphs plot). ``lamplighter-reports`` (a systemd timer) runs the reports
that are due; "Run now" in the UI runs one at once.

Security model:
- A run executes as the report's owner: their directory entry and roles
  are looked up again at every run, and the source's permissions (plus
  reports.manage) must still be held, or the run is recorded as denied.
- The table is produced by the same API code as a page download
  (export.py), so the same rules and columns apply.
- Every source has one fixed set of permissions (``Source.perms``): to
  create, run, see the results of, and receive by email a report of that
  source. Email goes only to directory people who hold them, so a report
  never reaches anyone who could not see it in the UI.
- Results are stored in Lamplighter's own schema, never in Robinhood's.
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
import time
import types
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import text

from .acl import AccessPolicy, Perm
from .audit import audit
from .auth import Principal
from .config import Settings, load_settings, read_secret
from .db import make_engine
from .export import QUIET, to_csv
from .ldapdir import Directory, DirectoryError

log = logging.getLogger(__name__)

PERIOD = {"hourly": 3600, "daily": 86400, "weekly": 7 * 86400}
SLACK = 300            # a run up to 5 minutes early counts as on time (timer jitter)
SCHEDULES = ("manual", "hourly", "daily", "weekly")


# --- sources -----------------------------------------------------------------

@dataclass(frozen=True)
class Param:
    name: str
    label: str
    kind: str                         # "choice", "int" or "text"
    default: Any = None
    choices: tuple[str, ...] = ()
    min: int | None = None
    max: int | None = None

    def clean(self, raw: Any) -> Any:
        if raw is None or raw == "":
            return self.default
        if self.kind == "choice":
            if raw not in self.choices:
                raise ValueError(f"{self.label}: one of {', '.join(self.choices)}")
            return raw
        if self.kind == "int":
            try:
                v = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{self.label}: a whole number")
            if (self.min is not None and v < self.min) or (self.max is not None and v > self.max):
                raise ValueError(f"{self.label}: between {self.min} and {self.max}")
            return v
        v = str(raw).strip()
        if len(v) > 255:
            raise ValueError(f"{self.label}: at most 255 characters")
        return v or self.default

    def as_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "kind": self.kind, "default": self.default,
                "choices": list(self.choices), "min": self.min, "max": self.max}


@dataclass(frozen=True)
class Source:
    key: str
    title: str
    perms: tuple[Perm, ...]
    fetch: Callable[[Any, Principal, dict], Any]       # (request-like, principal, params) -> Response
    params: tuple[Param, ...] = field(default=())

    def clean(self, raw: dict) -> dict:
        unknown = set(raw) - {p.name for p in self.params}
        if unknown:
            raise ValueError(f"unknown parameters: {', '.join(sorted(unknown))}")
        return {p.name: p.clean(raw.get(p.name)) for p in self.params}


def _users(req, p, a):
    from .usage_api import users
    return users(req, "json", p)


def _groups(req, p, a):
    from .usage_api import groups
    return groups(req, "json", p)


def _fileclasses(req, p, a):
    from .policies_api import policies
    return policies(req, "json", "fileclasses", p)


def _policy_status(req, p, a):
    from .policies_api import policies
    return policies(req, "json", "policies", p)


def _cold_owners(req, p, a):
    from .api import cold
    return cold(req, a["older_than"], a["limit"], "json", p)


def _cold_dirs(req, p, a):
    from .browse_api import coldest_directories
    return coldest_directories(req, a["older_than"], a["limit"], "json", p)


def _search(req, p, a):
    from .search_api import Criteria, dir_by_path, search_api
    dir_id = None
    if a.get("path"):
        with req.app.state.engine.connect() as c:
            dir_id = dir_by_path(c, a["path"])
            root = c.execute(text("SELECT value FROM VARS WHERE varname = 'RootId'")).scalar()
        if dir_id is None:
            raise HTTPException(404, f"No such directory: {a['path']}")
        root = (root.decode() if isinstance(root, (bytes, bytearray)) else str(root or "")).replace("/", ":")
        if dir_id.decode() == root:
            dir_id = None                   # the filesystem root: search everything
    crit = Criteria(**{k: v for k, v in a.items() if k != "path"})
    return search_api(req, dir_id.decode() if dir_id else None, crit, "json", p)


def _triage(table):
    def fetch(req, p, a):
        from .triage_api import triage
        return triage(req, a["window"], a["sort"], "json", table, p)
    return fetch


_AGE3 = ("90d", "1y", "3y")
SOURCES: dict[str, Source] = {s.key: s for s in [
    Source("users", "Users: usage per user", (Perm.USAGE_VIEW_AGGREGATES, Perm.USAGE_VIEW_ALL_USERS, Perm.COLD_EXPORT),
           _users),
    Source("groups", "Groups: usage per group", (Perm.USAGE_VIEW_AGGREGATES,), _groups),
    Source("fileclasses", "Fileclasses: entries and volume", (Perm.USAGE_VIEW_AGGREGATES,), _fileclasses),
    Source("policy-status", "Policy status: entries per status", (Perm.USAGE_VIEW_AGGREGATES,), _policy_status),
    Source("cold-owners", "Cold data: owners to contact", (Perm.COLD_VIEW, Perm.COLD_EXPORT), _cold_owners, (
        Param("older_than", "Not read for", "choice", "1y", _AGE3),
        Param("limit", "Owners", "int", 200, min=1, max=200))),
    Source("cold-directories", "Cold data: coldest directories", (Perm.COLD_VIEW, Perm.COLD_EXPORT), _cold_dirs, (
        Param("older_than", "Not read for", "choice", "1y", ("1y", "3y")),
        Param("limit", "Directories", "int", 200, min=1, max=200))),
    Source("search", "Search: files matching criteria", (Perm.PATHS_VIEW_ALL, Perm.COLD_EXPORT), _search, (
        Param("path", "In directory (default: the whole filesystem)", "text"),
        Param("type", "Type", "choice", "file", ("file", "dir", "any")),
        Param("owner", "Owner", "text"),
        Param("group", "Group", "text"),
        Param("name", "Name (shell pattern, e.g. *.tar)", "text"),
        Param("min_size", "At least (bytes)", "int", min=0),
        Param("max_size", "At most (bytes)", "int", min=0),
        Param("not_accessed_days", "Not read for (days)", "int", min=0, max=36500),
        Param("modified_within_days", "Modified within (days)", "int", min=0, max=36500))),
    Source("triage-jobs", "Triage: busiest jobs", (Perm.TRIAGE_VIEW,), _triage("jobs"), (
        Param("window", "Window", "choice", "24h", ("15m", "1h", "6h", "24h")),
        Param("sort", "Busiest by", "choice", "md", ("md", "io")))),
    Source("triage-targets", "Triage: Lustre targets", (Perm.TRIAGE_VIEW,), _triage("targets"), (
        Param("window", "Window", "choice", "24h", ("15m", "1h", "6h", "24h")),
        Param("sort", "Busiest by", "choice", "md", ("md", "io")))),
]}


def may_use(grants, source: Source) -> bool:
    """Whether someone may see results of (or receive) a report of this source."""
    return all(grants.allows(x) for x in source.perms)


# --- definitions ---------------------------------------------------------------

def validate(settings: Settings, body: dict) -> dict:
    """A report definition from the editor, checked and normalized."""
    name = str(body.get("name", "")).strip()
    if not name or len(name) > 100:
        raise ValueError("Name: 1 to 100 characters")
    source = SOURCES.get(body.get("source"))
    if source is None:
        raise ValueError("Unknown source")
    params = source.clean(body.get("params") or {})
    schedule = body.get("schedule", "daily")
    if schedule not in SCHEDULES:
        raise ValueError(f"Schedule: one of {', '.join(SCHEDULES)}")
    try:
        keep = int(body.get("keep_runs", 1))
    except (TypeError, ValueError):
        raise ValueError("Runs to keep: a whole number")
    if not 1 <= keep <= settings.reports.max_keep_runs:
        raise ValueError(f"Runs to keep: 1 to {settings.reports.max_keep_runs}")
    recipients = body.get("recipients") or []
    if not isinstance(recipients, list) or not all(isinstance(r, str) and 0 < len(r) <= 127 for r in recipients):
        raise ValueError("Recipients: a list of directory user names")
    if recipients and settings.reports.smtp is None:
        raise ValueError("Email is not configured on this server (reports.smtp)")
    chart = body.get("chart") or None
    if chart is not None:
        if not isinstance(chart, dict) or not isinstance(chart.get("value"), str) or not chart["value"]:
            raise ValueError("Chart: a value column")
        stack = chart.get("stack_by") or None
        top = int(chart.get("top") or 8)
        if not 1 <= top <= 8:              # eight categorical colours, then "Other"
            raise ValueError("Chart: 1 to 8 stacked series")
        chart = {"value": chart["value"][:64], "stack_by": stack[:64] if stack else None, "top": top}
    return {"name": name, "source": source.key, "params": params, "schedule": schedule,
            "keep_runs": keep, "recipients": sorted(set(recipients)), "chart": chart}


def _row(r) -> dict:
    return {"id": int(r.id), "name": r.name, "owner": r.owner, "source": r.source,
            "params": json.loads(r.params or "{}"), "schedule": r.schedule, "keep_runs": int(r.keep_runs),
            "recipients": json.loads(r.recipients or "[]"), "chart": json.loads(r.chart) if r.chart else None,
            "last_run_at": r.last_run_at}


def list_reports(store) -> list[dict]:
    with store.connect() as c:
        rows = c.execute(text("SELECT * FROM reports ORDER BY name")).all()
        last = {int(r.report_id): r for r in c.execute(text(
            "SELECT r.report_id, r.id, r.started_at, r.status, r.row_count, r.message, r.seconds FROM report_runs r "
            "JOIN (SELECT report_id, MAX(id) AS id FROM report_runs GROUP BY report_id) m ON m.id = r.id"))}
    out = []
    for r in rows:
        d = _row(r)
        lr = last.get(d["id"])
        d["last_run"] = None if lr is None else {
            "id": int(lr.id), "started_at": lr.started_at, "status": lr.status, "rows": int(lr.row_count),
            "message": lr.message, "seconds": float(lr.seconds) if lr.seconds is not None else None}
        out.append(d)
    return out


def get_report(store, report_id: int) -> dict | None:
    with store.connect() as c:
        r = c.execute(text("SELECT * FROM reports WHERE id = :i"), {"i": report_id}).first()
    return _row(r) if r else None


def save_report(store, d: dict, owner: str | None, report_id: int | None = None) -> int:
    vals = {"name": d["name"], "source": d["source"], "params": json.dumps(d["params"], sort_keys=True),
            "schedule": d["schedule"], "keep": d["keep_runs"], "rcpt": json.dumps(d["recipients"]),
            "chart": json.dumps(d["chart"]) if d["chart"] else None}
    with store.begin() as c:
        if report_id is None:
            res = c.execute(text(
                "INSERT INTO reports (name, owner, source, params, schedule, keep_runs, recipients, chart) "
                "VALUES (:name, :owner, :source, :params, :schedule, :keep, :rcpt, :chart)"), {**vals, "owner": owner})
            return int(res.lastrowid)
        # an edit makes the editor the owner: the report runs with their permissions
        c.execute(text(
            "UPDATE reports SET name = :name, owner = :owner, source = :source, params = :params, "
            "schedule = :schedule, keep_runs = :keep, recipients = :rcpt, chart = :chart WHERE id = :id"),
            {**vals, "owner": owner, "id": report_id})
        _trim(c, report_id, d["keep_runs"])
        return report_id


def delete_report(store, report_id: int) -> None:
    with store.begin() as c:
        c.execute(text("DELETE FROM reports WHERE id = :i"), {"i": report_id})


def _trim(c, report_id: int, keep: int) -> None:
    c.execute(text(
        "DELETE FROM report_runs WHERE report_id = :r AND id NOT IN (SELECT id FROM (SELECT id FROM report_runs "
        "WHERE report_id = :r ORDER BY id DESC LIMIT :k) newest)"), {"r": report_id, "k": keep})


# --- runs ------------------------------------------------------------------------

def principal_for(state, uid: str) -> Principal | None:
    user = state.directory.find_user(uid)
    if user is None:
        return None
    return Principal(user=user, grants=state.policy.grants_for(user.groups), session=None)


def _request(state):
    """What the API code reads from a request, for a run outside HTTP."""
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state), client=None,
                                 url=types.SimpleNamespace(path="/reports"))


def run(state, report: dict, trigger: str) -> dict:
    """Run one report as its owner, store the run, apply retention and send
    email. Returns the run's summary; never raises for a failed run."""
    started, t0 = int(time.time()), time.monotonic()
    source = SOURCES.get(report["source"])
    status, message, columns, rows = "ok", None, [], []
    try:
        p = principal_for(state, report["owner"])
        missing = [] if p is None else [x.value for x in (Perm.REPORTS_MANAGE, *source.perms)
                                        if not p.grants.allows(x)]
        if p is None:
            status, message = "denied", f"{report['owner']} is no longer in the directory"
        elif missing:
            status, message = "denied", f"{report['owner']} no longer holds: {', '.join(missing)}"
        else:
            token = QUIET.set(True)
            try:
                resp = source.fetch(_request(state), p, source.clean(report["params"]))
            finally:
                QUIET.reset(token)
            body = json.loads(resp.body)
            columns, rows = body["columns"], body["rows"]
    except HTTPException as exc:
        status, message = ("denied" if exc.status_code == 403 else "error"), str(exc.detail)
    except DirectoryError as exc:
        status, message = "error", f"directory unavailable: {exc}"
    except Exception as exc:                      # a failed run is recorded, not fatal
        log.exception("report %s failed", report["name"])
        status, message = "error", str(exc)[:1000] or type(exc).__name__
    if status == "ok" and report["recipients"]:
        message = send_email(state, report, source, columns, rows, started)
    seconds = round(time.monotonic() - t0, 2)
    with state.store_engine.begin() as c:
        res = c.execute(text(
            "INSERT INTO report_runs (report_id, started_at, seconds, status, trigger_by, row_count, message, data) "
            "VALUES (:r, :t, :s, :st, :tr, :n, :m, :d)"),
            {"r": report["id"], "t": started, "s": seconds, "st": status, "tr": trigger, "n": len(rows),
             "m": message, "d": json.dumps({"columns": columns, "rows": rows}, default=str) if status == "ok" else None})
        c.execute(text("UPDATE reports SET last_run_at = :t WHERE id = :r"), {"t": started, "r": report["id"]})
        _trim(c, report["id"], report["keep_runs"])
    audit("report.run", user=report["owner"], report=report["name"], source=report["source"], trigger=trigger,
          outcome=status, rows=len(rows), message=message)
    return {"id": int(res.lastrowid), "started_at": started, "status": status, "rows": len(rows),
            "message": message, "seconds": seconds}


def due(reports: list[dict], now: int) -> list[dict]:
    return [r for r in reports if r["schedule"] != "manual"
            and (r["last_run_at"] is None or now - r["last_run_at"] >= PERIOD[r["schedule"]] - SLACK)]


def runs(store, report_id: int) -> list[dict]:
    with store.connect() as c:
        return [{"id": int(r.id), "started_at": r.started_at, "status": r.status, "trigger": r.trigger_by,
                 "rows": int(r.row_count), "message": r.message,
                 "seconds": float(r.seconds) if r.seconds is not None else None}
                for r in c.execute(text("SELECT id, started_at, status, trigger_by, row_count, message, seconds "
                                        "FROM report_runs WHERE report_id = :r ORDER BY id DESC"), {"r": report_id})]


def run_data(store, report_id: int, run_id: int | None = None) -> dict | None:
    """A run's table (the newest successful run by default)."""
    with store.connect() as c:
        if run_id is None:
            r = c.execute(text("SELECT id, started_at, data FROM report_runs WHERE report_id = :r AND status = 'ok' "
                               "ORDER BY id DESC LIMIT 1"), {"r": report_id}).first()
        else:
            r = c.execute(text("SELECT id, started_at, data FROM report_runs WHERE report_id = :r AND id = :i "
                               "AND status = 'ok'"), {"r": report_id, "i": run_id}).first()
    if r is None:
        return None
    return {"run_id": int(r.id), "started_at": r.started_at, **json.loads(r.data)}


def series(store, report: dict) -> dict | None:
    """The custom graph: report.chart's value column over the kept runs,
    summed per run, or stacked by a column (top N keys, the rest "Other")."""
    chart = report["chart"]
    if not chart:
        return None
    value, stack, top = chart["value"], chart["stack_by"], chart["top"]
    points: list[tuple[int, dict]] = []
    with store.connect() as c:
        for started, data in c.execute(text("SELECT started_at, data FROM report_runs WHERE report_id = :r "
                                             "AND status = 'ok' ORDER BY started_at"), {"r": report["id"]}):
            sums: dict[str, float] = {}
            for row in json.loads(data)["rows"]:
                v = row.get(value)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    k = str(row.get(stack)) if stack else value
                    sums[k] = sums.get(k, 0) + v
            points.append((int(started), sums))
    totals: dict[str, float] = {}
    for _, sums in points:
        for k, v in sums.items():
            totals[k] = totals.get(k, 0) + v
    keys = [k for k, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:top]]
    other = len(totals) > len(keys)
    out = {"value": value, "stack_by": stack, "times": [t for t, _ in points], "series": []}
    for k in keys:
        out["series"].append({"name": k, "values": [s.get(k, 0) for _, s in points]})
    if other:
        out["series"].append({"name": "Other", "values": [sum(v for kk, v in s.items() if kk not in keys)
                                                          for _, s in points]})
    return out


# --- email -------------------------------------------------------------------------

def send_email(state, report: dict, source: Source, columns: list, rows: list, started: int) -> str:
    """Email the table as CSV to the report's recipients who may see it.
    Returns what happened, for the run record."""
    smtp = state.settings.reports.smtp
    if smtp is None:
        return "not emailed: email is not configured (reports.smtp)"
    to, skipped = [], []
    for uid in report["recipients"]:
        try:
            person = principal_for(state, uid)
        except DirectoryError:
            person = None
        if person is None or not person.user.mail:
            skipped.append(f"{uid} (no address)")
        elif not may_use(person.grants, source):
            skipped.append(f"{uid} (may not see this report)")
        else:
            to.append(person.user.mail)
    if not to:
        return "not emailed: " + ", ".join(skipped)
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(started))
    msg = EmailMessage()
    msg["Subject"] = f"Lamplighter report: {report['name']}"
    msg["From"] = smtp.from_address
    msg["To"] = ", ".join(sorted(set(to)))
    msg.set_content(
        f"{report['name']} ({source.title}), run {when}: {len(rows)} rows, attached as CSV.\n\n"
        f"Results and history: {state.settings.server.base_url.rstrip('/')}/reports/{report['id']}\n\n"
        "You receive this because the report's owner added you as a recipient.\n")
    stamp = time.strftime("%Y%m%d-%H%M", time.gmtime(started))
    msg.add_attachment(to_csv(columns, rows).encode(), maintype="text", subtype="csv",
                       filename=f"lamplighter-report-{report['id']}-{stamp}.csv")
    try:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as s:
            if smtp.starttls:
                s.starttls(context=ssl.create_default_context())
            if smtp.username:
                s.login(smtp.username, read_secret(smtp.password_file) if smtp.password_file else "")
            s.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        return f"email failed: {exc}"
    return f"emailed to {len(set(to))}" + (f"; skipped: {', '.join(skipped)}" if skipped else "")


# --- lamplighter-reports -------------------------------------------------------------

def make_state(settings: Settings):
    """What the API code needs, built for a command-line run."""
    return types.SimpleNamespace(
        settings=settings, directory=Directory(settings.ldap), policy=AccessPolicy(settings.access),
        engine=make_engine(settings.database, read_timeout=None),
        store_engine=make_engine(settings.store, read_timeout=None))


def run_due(settings: Settings, only: str | None = None) -> int:
    """Run the reports that are due (or the one named ``only``, due or not)."""
    from .store import migrate
    state = make_state(settings)
    migrate(state.store_engine)
    with state.store_engine.connect() as lock:
        if lock.execute(text("SELECT GET_LOCK('lamplighter_reports', 0)")).scalar() != 1:
            log.info("another lamplighter-reports run is in progress; skipping")
            return 0
        try:
            reports = list_reports(state.store_engine)
            todo = [r for r in reports if r["name"] == only] if only else due(reports, int(time.time()))
            if only and not todo:
                raise SystemExit(f"no report named {only!r}")
            for r in todo:
                out = run(state, r, "schedule" if not only else "command line")
                log.info("report %r: %s, %d rows in %.1fs%s", r["name"], out["status"], out["rows"],
                         out["seconds"], f" ({out['message']})" if out["message"] else "")
            return len(todo)
        finally:
            lock.execute(text("SELECT RELEASE_LOCK('lamplighter_reports')"))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="lamplighter-reports",
                                 description="Run Lamplighter's scheduled reports that are due.")
    ap.add_argument("--config", help="config file (default: $LAMPLIGHTER_WEBUI_CONFIG)")
    ap.add_argument("--run", metavar="NAME", help="run this report now, due or not")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)
    try:
        run_due(load_settings(args.config), args.run)
    except SystemExit:
        raise
    except Exception as exc:
        log.error("lamplighter-reports failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
