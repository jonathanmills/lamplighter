#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Check plugins for the Lamplighter special agent (Checkmk agent-based API v2).

The Lamplighter web UI only reports facts; every threshold lives here, set
through the rulesets in ../rulesets/lamplighter.py.

Services:
  Lamplighter            database, last scan (status, age, errors), entries,
                         changelog backlog (Lustre with changelogs only)
  Lamplighter collector  time since the last successful collector run
  Lamplighter usage      volume, files, and data not read for 1 / 3 years
  Lamplighter fileclass <name>
                         entries and volume of one Robinhood fileclass, and
                         how many of them a classifier alert policy marked
  Lamplighter policy <name>
                         entries per status of one Robinhood policy
  Lamplighter jobstats   metadata requests and bytes per second (Lustre
                         jobstats), and sources not reporting
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Any

from cmk.agent_based.v2 import (
    AgentSection,
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    Metric,
    Result,
    Service,
    State,
    StringTable,
    check_levels,
    render,
)

Section = Mapping[str, Any]


def parse_json(string_table: StringTable) -> Section | None:
    """Each section is one line of JSON (sep(0): the whole line is one field)."""
    if not string_table or not string_table[0]:
        return None
    try:
        data = json.loads(string_table[0][0])
    except (ValueError, IndexError):
        return None
    return data if isinstance(data, dict) else None


agent_section_lamplighter_status = AgentSection(name="lamplighter_status", parse_function=parse_json)
agent_section_lamplighter_collector = AgentSection(name="lamplighter_collector", parse_function=parse_json)
agent_section_lamplighter_usage = AgentSection(name="lamplighter_usage", parse_function=parse_json)
agent_section_lamplighter_policies = AgentSection(name="lamplighter_policies", parse_function=parse_json)
agent_section_lamplighter_jobstats = AgentSection(name="lamplighter_jobstats", parse_function=parse_json)


def discover_single(section: Section) -> DiscoveryResult:
    yield Service()


# --- Lamplighter (status) ----------------------------------------------------------

def check_lamplighter_status(params: Mapping[str, Any], section: Section) -> CheckResult:
    db = section.get("database", {})
    if not db.get("reachable", False):
        yield Result(state=State.CRIT,
                     summary=f"Lamplighter database unreachable ({db.get('error', 'unknown error')})")
        return
    fs = section.get("filesystem")
    if fs:
        yield Result(state=State.OK, summary=f"Filesystem {fs}")

    scan = section.get("last_scan", {})
    status = scan.get("status")
    if status is None:
        yield Result(state=State.WARN, summary="No scan recorded yet")
    elif status != "done":
        yield Result(state=State.WARN, summary=f"Last scan status: {status}")
    else:
        yield Result(state=State.OK, summary="Last scan completed")

    if "age_seconds" in scan:
        yield from check_levels(scan["age_seconds"], levels_upper=params.get("scan_age"),
                                metric_name="lamplighter_scan_age", render_func=render.timespan,
                                label="Time since last scan")
    if "errors" in scan:
        yield from check_levels(scan["errors"], levels_upper=params.get("scan_errors"),
                                metric_name="lamplighter_scan_errors", render_func=str, label="Scan errors")
    if "entries" in scan:
        yield Result(state=State.OK, notice=f"Entries scanned: {scan['entries']}")
        yield Metric("lamplighter_entries", scan["entries"])

    cl = section.get("changelog") or {}
    if "backlog_records" in cl:
        yield from check_levels(cl["backlog_records"], levels_upper=params.get("changelog_backlog"),
                                metric_name="lamplighter_changelog_backlog", render_func=str,
                                label="Changelog backlog (records)")


check_plugin_lamplighter_status = CheckPlugin(
    name="lamplighter_status",
    sections=["lamplighter_status"],
    service_name="Lamplighter",
    discovery_function=discover_single,
    check_function=check_lamplighter_status,
    check_ruleset_name="lamplighter_status",
    check_default_parameters={
        "scan_age": ("fixed", (5 * 86400.0, 8 * 86400.0)),
        "scan_errors": ("fixed", (1, 1000)),
        "changelog_backlog": ("fixed", (1_000_000, 10_000_000)),
    },
)


# --- Lamplighter collector ---------------------------------------------------------

def check_lamplighter_collector(params: Mapping[str, Any], section: Section) -> CheckResult:
    if "error" in section:
        yield Result(state=State.CRIT, summary=f"Collector data unavailable ({section['error']})")
        return
    last = section.get("last_run") or {}
    if not last:
        yield Result(state=State.WARN, summary="The collector has not run yet")
        return
    if last.get("status") == "failed":
        yield Result(state=State.WARN, summary=f"Last run failed: {last.get('error') or 'no details'}")
    elif last.get("status") == "running":
        yield Result(state=State.OK, summary="A run is in progress")
    finished = section.get("last_ok_finished")
    if finished is None:
        yield Result(state=State.CRIT, summary="No successful collector run")
        return
    # The check runs right after the agent fetched the data: use this server's clock.
    yield from check_levels(max(0.0, time.time() - float(finished)), levels_upper=params.get("collector_age"),
                            metric_name="lamplighter_collector_age", render_func=render.timespan,
                            label="Time since last successful run")
    if last.get("entries") is not None:
        yield Result(state=State.OK, notice=f"Files covered by the last run: {last['entries']}")


check_plugin_lamplighter_collector = CheckPlugin(
    name="lamplighter_collector",
    sections=["lamplighter_collector"],
    service_name="Lamplighter collector",
    discovery_function=discover_single,
    check_function=check_lamplighter_collector,
    check_ruleset_name="lamplighter_collector",
    check_default_parameters={"collector_age": ("fixed", (26 * 3600.0, 50 * 3600.0))},
)


# --- Lamplighter usage ---------------------------------------------------------------

def check_lamplighter_usage(params: Mapping[str, Any], section: Section) -> CheckResult:
    by_type = (section.get("usage") or {}).get("by_type", {})
    total = sum(int(t.get("bytes", 0)) for t in by_type.values())
    files = int(by_type.get("file", {}).get("entries", 0))
    yield Result(state=State.OK, summary=f"Volume: {render.bytes(total)}, files: {files}")
    yield Metric("lamplighter_bytes", total)
    yield Metric("lamplighter_files", files)

    age = section.get("age") or {}
    bands = {int(b["band"]): int(b["bytes"]) for b in age.get("bands", [])}
    if bands:
        cold_1y = bands.get(4, 0) + bands.get(5, 0)
        cold_3y = bands.get(5, 0)
        yield from check_levels(cold_1y, levels_upper=params.get("cold_1y_bytes"),
                                metric_name="lamplighter_cold_1y_bytes", render_func=render.bytes,
                                label="Not read in over 1 year")
        yield from check_levels(cold_3y, levels_upper=params.get("cold_3y_bytes"),
                                metric_name="lamplighter_cold_3y_bytes", render_func=render.bytes,
                                label="Not read in over 3 years", notice_only=True)
    else:
        yield Result(state=State.OK, notice="Age figures appear after the collector's first run")


check_plugin_lamplighter_usage = CheckPlugin(
    name="lamplighter_usage",
    sections=["lamplighter_usage"],
    service_name="Lamplighter usage",
    discovery_function=discover_single,
    check_function=check_lamplighter_usage,
    check_ruleset_name="lamplighter_usage",
    check_default_parameters={"cold_1y_bytes": ("no_levels", None), "cold_3y_bytes": ("no_levels", None)},
)


# --- Lamplighter fileclass <name> ----------------------------------------------------
# Robinhood alert policies are used only as classifiers: they mark entries
# "alert" or "clear". The thresholds on those counts live here.

def discover_lamplighter_fileclass(section: Section) -> DiscoveryResult:
    for name in sorted(section.get("fileclasses") or {}):
        if name:                                  # "" = entries in no fileclass
            yield Service(item=name)


def _alert_entries(cls: Mapping[str, Any]) -> int:
    return sum(int(per_status.get("alert", 0)) for per_status in (cls.get("statuses") or {}).values())


def check_lamplighter_fileclass(item: str, params: Mapping[str, Any], section: Section) -> CheckResult:
    cls = (section.get("fileclasses") or {}).get(item)
    if cls is None:
        return                                    # Checkmk reports the item as missing
    yield from check_levels(int(cls.get("entries", 0)), levels_upper=params.get("entries"),
                            metric_name="lamplighter_class_entries", render_func=str, label="Entries")
    yield from check_levels(int(cls.get("bytes", 0)), levels_upper=params.get("bytes"),
                            metric_name="lamplighter_class_bytes", render_func=render.bytes, label="Volume")
    statuses = cls.get("statuses") or {}
    if statuses:
        yield from check_levels(_alert_entries(cls), levels_upper=params.get("alerts"),
                                metric_name="lamplighter_class_alerts", render_func=str,
                                label="Marked 'alert' by a classifier policy")
    for policy, per_status in sorted(statuses.items()):
        shown = ", ".join(f"{st}: {n}" for st, n in sorted(per_status.items()) if st)
        if shown:
            yield Result(state=State.OK, notice=f"Policy {policy}: {shown}")
    if section.get("as_of"):
        yield Result(state=State.OK, notice=f"As of the collector run at {render.datetime(section['as_of'])}")


check_plugin_lamplighter_fileclass = CheckPlugin(
    name="lamplighter_fileclass",
    sections=["lamplighter_policies"],
    service_name="Lamplighter fileclass %s",
    discovery_function=discover_lamplighter_fileclass,
    check_function=check_lamplighter_fileclass,
    check_ruleset_name="lamplighter_fileclass",
    check_default_parameters={"entries": ("no_levels", None), "bytes": ("no_levels", None),
                              "alerts": ("no_levels", None)},
)


# --- Lamplighter policy <name> -------------------------------------------------------

def _metric_safe(status: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", status) or "not_set"


def discover_lamplighter_policy(section: Section) -> DiscoveryResult:
    for name in sorted(section.get("policies") or {}):
        yield Service(item=name)


def check_lamplighter_policy(item: str, params: Mapping[str, Any], section: Section) -> CheckResult:
    policy = (section.get("policies") or {}).get(item)
    if policy is None:
        return
    entries = {st: int(v.get("entries", 0)) for st, v in (policy.get("statuses") or {}).items()}
    shown = ", ".join(f"{st or 'not set'}: {n}" for st, n in sorted(entries.items(), key=lambda kv: (kv[0] == "", kv[0])))
    yield Result(state=State.OK, summary=f"Entries per status: {shown or 'none'}")
    levels = {str(rule.get("status", "")): rule.get("levels") for rule in params.get("status_levels") or []}
    for status, n in sorted(entries.items()):
        if status not in levels:
            yield Metric(f"lamplighter_status_{_metric_safe(status)}", n)
    # Statuses with levels are checked even when no entry has them (count 0).
    for status, lv in sorted(levels.items()):
        yield from check_levels(entries.get(status, 0), levels_upper=lv,
                                metric_name=f"lamplighter_status_{_metric_safe(status)}", render_func=str,
                                label=f"Status '{status or 'not set'}'")


check_plugin_lamplighter_policy = CheckPlugin(
    name="lamplighter_policy",
    sections=["lamplighter_policies"],
    service_name="Lamplighter policy %s",
    discovery_function=discover_lamplighter_policy,
    check_function=check_lamplighter_policy,
    check_ruleset_name="lamplighter_policy",
    check_default_parameters={"status_levels": []},
)


# --- Lamplighter jobstats ------------------------------------------------------------

def discover_lamplighter_jobstats(section: Section) -> DiscoveryResult:
    if section.get("configured"):
        yield Service()


def check_lamplighter_jobstats(params: Mapping[str, Any], section: Section) -> CheckResult:
    if "error" in section:
        yield Result(state=State.UNKNOWN, summary=f"Jobstats unavailable ({section['error']})")
        return
    window = section.get("window_seconds", 300)
    yield from check_levels(float(section.get("md_ops_per_second", 0.0)), levels_upper=params.get("md_ops_per_second"),
                            metric_name="lamplighter_md_ops_rate", render_func=lambda v: f"{v:.1f}/s",
                            label=f"Metadata requests (last {window // 60} min)")
    yield from check_levels(float(section.get("read_bytes_per_second", 0.0)), levels_upper=params.get("read_bytes_per_second"),
                            metric_name="lamplighter_read_rate", render_func=render.iobandwidth, label="Read")
    yield from check_levels(float(section.get("write_bytes_per_second", 0.0)), levels_upper=params.get("write_bytes_per_second"),
                            metric_name="lamplighter_write_rate", render_func=render.iobandwidth, label="Written")
    yield from check_levels(int(section.get("sources_not_reporting", 0)), levels_upper=params.get("sources_not_reporting"),
                            metric_name="lamplighter_jobstats_stale_sources", render_func=str,
                            label=f"Sources not reporting (of {section.get('sources', 0)})")
    top = section.get("top_md_job")
    if top:
        yield Result(state=State.OK, notice=f"Busiest job by metadata: {top['job_id']} ({top['md_ops_per_second']:.1f}/s)")


check_plugin_lamplighter_jobstats = CheckPlugin(
    name="lamplighter_jobstats",
    sections=["lamplighter_jobstats"],
    service_name="Lamplighter jobstats",
    discovery_function=discover_lamplighter_jobstats,
    check_function=check_lamplighter_jobstats,
    check_ruleset_name="lamplighter_jobstats",
    check_default_parameters={"md_ops_per_second": ("no_levels", None), "read_bytes_per_second": ("no_levels", None),
                              "write_bytes_per_second": ("no_levels", None),
                              "sources_not_reporting": ("fixed", (1, 3))},
)
