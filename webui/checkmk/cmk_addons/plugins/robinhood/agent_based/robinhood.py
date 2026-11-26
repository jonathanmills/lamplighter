#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Check plugins for the Robinhood special agent (Checkmk agent-based API v2).

The Robinhood web UI only reports facts; every threshold lives here, set
through the rulesets in ../rulesets/robinhood.py.

Services:
  Robinhood            database, last scan (status, age, errors), entries,
                       changelog backlog (Lustre with changelogs only)
  Robinhood collector  time since the last successful collector run
  Robinhood usage      volume, files, and data not read for 1 / 3 years
"""

from __future__ import annotations

import json
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


agent_section_robinhood_status = AgentSection(name="robinhood_status", parse_function=parse_json)
agent_section_robinhood_collector = AgentSection(name="robinhood_collector", parse_function=parse_json)
agent_section_robinhood_usage = AgentSection(name="robinhood_usage", parse_function=parse_json)


def discover_single(section: Section) -> DiscoveryResult:
    yield Service()


# --- Robinhood (status) ----------------------------------------------------------

def check_robinhood_status(params: Mapping[str, Any], section: Section) -> CheckResult:
    db = section.get("database", {})
    if not db.get("reachable", False):
        yield Result(state=State.CRIT,
                     summary=f"Robinhood database unreachable ({db.get('error', 'unknown error')})")
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
                                metric_name="robinhood_scan_age", render_func=render.timespan,
                                label="Time since last scan")
    if "errors" in scan:
        yield from check_levels(scan["errors"], levels_upper=params.get("scan_errors"),
                                metric_name="robinhood_scan_errors", render_func=str, label="Scan errors")
    if "entries" in scan:
        yield Result(state=State.OK, notice=f"Entries scanned: {scan['entries']}")
        yield Metric("robinhood_entries", scan["entries"])

    cl = section.get("changelog") or {}
    if "backlog_records" in cl:
        yield from check_levels(cl["backlog_records"], levels_upper=params.get("changelog_backlog"),
                                metric_name="robinhood_changelog_backlog", render_func=str,
                                label="Changelog backlog (records)")


check_plugin_robinhood_status = CheckPlugin(
    name="robinhood_status",
    sections=["robinhood_status"],
    service_name="Robinhood",
    discovery_function=discover_single,
    check_function=check_robinhood_status,
    check_ruleset_name="robinhood_status",
    check_default_parameters={
        "scan_age": ("fixed", (5 * 86400.0, 8 * 86400.0)),
        "scan_errors": ("fixed", (1, 1000)),
        "changelog_backlog": ("fixed", (1_000_000, 10_000_000)),
    },
)


# --- Robinhood collector ---------------------------------------------------------

def check_robinhood_collector(params: Mapping[str, Any], section: Section) -> CheckResult:
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
                            metric_name="robinhood_collector_age", render_func=render.timespan,
                            label="Time since last successful run")
    if last.get("entries") is not None:
        yield Result(state=State.OK, notice=f"Files covered by the last run: {last['entries']}")


check_plugin_robinhood_collector = CheckPlugin(
    name="robinhood_collector",
    sections=["robinhood_collector"],
    service_name="Robinhood collector",
    discovery_function=discover_single,
    check_function=check_robinhood_collector,
    check_ruleset_name="robinhood_collector",
    check_default_parameters={"collector_age": ("fixed", (26 * 3600.0, 50 * 3600.0))},
)


# --- Robinhood usage ---------------------------------------------------------------

def check_robinhood_usage(params: Mapping[str, Any], section: Section) -> CheckResult:
    by_type = (section.get("usage") or {}).get("by_type", {})
    total = sum(int(t.get("bytes", 0)) for t in by_type.values())
    files = int(by_type.get("file", {}).get("entries", 0))
    yield Result(state=State.OK, summary=f"Volume: {render.bytes(total)}, files: {files}")
    yield Metric("robinhood_bytes", total)
    yield Metric("robinhood_files", files)

    age = section.get("age") or {}
    bands = {int(b["band"]): int(b["bytes"]) for b in age.get("bands", [])}
    if bands:
        cold_1y = bands.get(4, 0) + bands.get(5, 0)
        cold_3y = bands.get(5, 0)
        yield from check_levels(cold_1y, levels_upper=params.get("cold_1y_bytes"),
                                metric_name="robinhood_cold_1y_bytes", render_func=render.bytes,
                                label="Not read in over 1 year")
        yield from check_levels(cold_3y, levels_upper=params.get("cold_3y_bytes"),
                                metric_name="robinhood_cold_3y_bytes", render_func=render.bytes,
                                label="Not read in over 3 years", notice_only=True)
    else:
        yield Result(state=State.OK, notice="Age figures appear after the collector's first run")


check_plugin_robinhood_usage = CheckPlugin(
    name="robinhood_usage",
    sections=["robinhood_usage"],
    service_name="Robinhood usage",
    discovery_function=discover_single,
    check_function=check_robinhood_usage,
    check_ruleset_name="robinhood_usage",
    check_default_parameters={"cold_1y_bytes": ("no_levels", None), "cold_3y_bytes": ("no_levels", None)},
)
