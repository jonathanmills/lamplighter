# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Tests for the Checkmk plugin, run against Checkmk 2.5's real plugin APIs
(extracted from the Checkmk image; see ../README.md) under Python 3.13.

The integration test runs the special agent against the live dev
environment (Keycloak + web UI), then feeds its output to the checks.
"""

import json
import os
import time

import pytest

from cmk.agent_based.v2 import Metric, Result, Service, State
from cmk.server_side_calls.v1 import HostConfig, IPv4Config, Secret

from cmk_addons.plugins.lamplighter.agent_based import lamplighter as ab
from cmk_addons.plugins.lamplighter.rulesets import lamplighter as rs
from cmk_addons.plugins.lamplighter.server_side_calls import lamplighter as ssc
from cmk_addons.plugins.lamplighter.special_agent import agent_lamplighter as agent

STATUS = ab.check_plugin_lamplighter_status.check_default_parameters
COLLECTOR = ab.check_plugin_lamplighter_collector.check_default_parameters
USAGE = ab.check_plugin_lamplighter_usage.check_default_parameters
FILECLASS = ab.check_plugin_lamplighter_fileclass.check_default_parameters
POLICY = ab.check_plugin_lamplighter_policy.check_default_parameters
JOBSTATS = ab.check_plugin_lamplighter_jobstats.check_default_parameters


def worst(results) -> State:
    return State.worst(*[r.state for r in results if isinstance(r, Result)])


def section_lines(output: str, name: str) -> list[list[str]]:
    lines = output.splitlines()
    i = lines.index(f"<<<{name}:sep(0)>>>")
    return [[lines[i + 1]]]


# --- special agent against the live dev environment ----------------------------

@pytest.fixture(scope="module")
def agent_output(capfd_module=None):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = agent.main(["--url", "https://webui.test:8443",
                         "--token-url", "https://keycloak.test:9443/realms/lamplighter/protocol/openid-connect/token",
                         "--client-id", "lux-checkmk", "--client-secret", "dev-checkmk-client-secret",
                         "--ca-file", "/certs/ca.pem"])
    assert rc == 0
    return buf.getvalue()


def test_agent_prints_its_json_sections(agent_output):
    for name in ("lamplighter_status", "lamplighter_collector", "lamplighter_usage",
                 "lamplighter_policies", "lamplighter_jobstats"):
        data = json.loads(section_lines(agent_output, name)[0][0])
        assert isinstance(data, dict)


def test_live_data_gives_ok_services(agent_output):
    status = ab.parse_json(section_lines(agent_output, "lamplighter_status"))
    collector = ab.parse_json(section_lines(agent_output, "lamplighter_collector"))
    usage = ab.parse_json(section_lines(agent_output, "lamplighter_usage"))
    for plugin, sec, params in ((ab.check_lamplighter_status, status, STATUS),
                                (ab.check_lamplighter_collector, collector, COLLECTOR),
                                (ab.check_lamplighter_usage, usage, USAGE)):
        results = list(plugin(params, sec))
        assert results and worst(results) is State.OK, results
    metrics = {m.name for m in ab.check_lamplighter_usage(USAGE, usage) if isinstance(m, Metric)}
    assert {"lamplighter_bytes", "lamplighter_files", "lamplighter_cold_1y_bytes"} <= metrics


def test_agent_fails_cleanly_on_a_wrong_secret(capsys):
    rc = agent.main(["--url", "https://webui.test:8443",
                     "--token-url", "https://keycloak.test:9443/realms/lamplighter/protocol/openid-connect/token",
                     "--client-id", "lux-checkmk", "--client-secret", "wrong", "--ca-file", "/certs/ca.pem"])
    assert rc == 1 and "HTTP 401" in capsys.readouterr().err


def test_agent_refuses_an_untrusted_certificate(capsys):
    rc = agent.main(["--url", "https://webui.test:8443",
                     "--token-url", "https://keycloak.test:9443/realms/lamplighter/protocol/openid-connect/token",
                     "--client-id", "lux-checkmk", "--client-secret", "dev-checkmk-client-secret"])
    assert rc == 1 and "CERTIFICATE_VERIFY_FAILED" in capsys.readouterr().err


# --- checks on hand-made sections ----------------------------------------------

def test_discovery_is_one_service():
    assert list(ab.discover_single({})) == [Service()]


def test_database_down_is_crit():
    assert worst(ab.check_lamplighter_status(STATUS, {"database": {"reachable": False, "error": "OperationalError"}})) is State.CRIT


@pytest.mark.parametrize("age_days, state", [(1, State.OK), (6, State.WARN), (9, State.CRIT)])
def test_scan_age_levels(age_days, state):
    sec = {"database": {"reachable": True}, "last_scan": {"status": "done", "age_seconds": age_days * 86400, "errors": 0}}
    assert worst(ab.check_lamplighter_status(STATUS, sec)) is state


def test_changelog_backlog_levels():
    sec = {"database": {"reachable": True}, "last_scan": {"status": "done", "age_seconds": 60, "errors": 0},
           "changelog": {"backlog_records": 2_000_000}}
    assert worst(ab.check_lamplighter_status(STATUS, sec)) is State.WARN


def test_collector_failed_and_stale():
    old = time.time() - 60 * 3600
    sec = {"last_run": {"status": "failed", "error": "TimeoutError"}, "last_ok_finished": old}
    results = list(ab.check_lamplighter_collector(COLLECTOR, sec))
    assert worst(results) is State.CRIT
    assert any("failed" in r.summary for r in results if isinstance(r, Result))


def test_collector_never_ran():
    assert worst(ab.check_lamplighter_collector(COLLECTOR, {})) is State.WARN


def test_cold_data_levels_when_configured():
    tib = 1024**4
    sec = {"usage": {"by_type": {"file": {"entries": 10, "bytes": 600 * tib}}},
           "age": {"bands": [{"band": 4, "bytes": 200 * tib}, {"band": 5, "bytes": 400 * tib}]}}
    params = {**USAGE, "cold_1y_bytes": ("fixed", (100 * tib, 500 * tib))}
    assert worst(ab.check_lamplighter_usage(params, sec)) is State.CRIT
    assert worst(ab.check_lamplighter_usage(USAGE, sec)) is State.OK      # no levels by default


def test_bad_agent_output_is_ignored():
    assert ab.parse_json([["not json"]]) is None and ab.parse_json([]) is None


# --- server-side call and rulesets ---------------------------------------------

def test_secret_is_passed_by_reference_only():
    host = HostConfig(name="lamplighter", ipv4_config=IPv4Config(address="10.0.0.1"))
    params = {"url": "https://lamplighter.example.org", "token_url": "https://kc.example.org/t",
              "client_id": "lux-checkmk", "client_secret": Secret(42), "timeout": 20}
    [cmd] = list(ssc.special_agent_lamplighter(params, host))
    args = cmd.command_arguments
    i = args.index("--client-secret-id")
    assert isinstance(args[i + 1], Secret) and args[i + 1].pass_safely
    assert "--client-secret" not in args


def test_rulesets_build():
    for spec in (rs.rule_spec_special_agent_lamplighter, rs.rule_spec_lamplighter_status,
                 rs.rule_spec_lamplighter_collector, rs.rule_spec_lamplighter_usage,
                 rs.rule_spec_lamplighter_fileclass, rs.rule_spec_lamplighter_policy,
                 rs.rule_spec_lamplighter_jobstats):
        form = spec.parameter_form()
        assert form.elements
    assert set(rs._status_form().elements) == set(STATUS)
    assert set(rs._collector_form().elements) == set(COLLECTOR)
    assert set(rs._usage_form().elements) == set(USAGE)
    assert set(rs._fileclass_form().elements) == set(FILECLASS)
    assert set(rs._policy_form().elements) == set(POLICY)
    assert set(rs._jobstats_form().elements) == set(JOBSTATS)


# --- fileclasses, policies and jobstats -------------------------------------------

POLICIES = {"as_of": 1795000000, "fileclasses": {
    "": {"entries": 7168, "bytes": 10, "statuses": {"alert": {"": 7168}}},
    "outsized": {"entries": 39, "bytes": 1429365116108, "statuses": {"alert": {"alert": 39}}},
    "scratch": {"entries": 9063, "bytes": 43830000, "statuses": {"alert": {"": 9063}}}},
    "policies": {"alert": {"field": "alert_status", "statuses": {
        "": {"entries": 24474, "bytes": 1}, "alert": {"entries": 39, "bytes": 2}}},
        "checksum": {"field": "checksum_status", "statuses": {"ok": {"entries": 900, "bytes": 3}}}}}


def test_live_policy_and_jobstats_services(agent_output):
    pol = ab.parse_json(section_lines(agent_output, "lamplighter_policies"))
    items = {s.item for s in ab.discover_lamplighter_fileclass(pol)}
    assert "outsized" in items and "" not in items
    assert worst(ab.check_lamplighter_fileclass("outsized", FILECLASS, pol)) == State.OK
    assert {s.item for s in ab.discover_lamplighter_policy(pol)} >= {"alert"}
    js = ab.parse_json(section_lines(agent_output, "lamplighter_jobstats"))
    assert list(ab.discover_lamplighter_jobstats(js)) == [Service()]


def test_fileclass_discovery_skips_unclassified():
    assert [s.item for s in ab.discover_lamplighter_fileclass(POLICIES)] == ["outsized", "scratch"]


def test_fileclass_alerts_ok_by_default_and_levels_when_set():
    results = list(ab.check_lamplighter_fileclass("outsized", FILECLASS, POLICIES))
    assert worst(results) == State.OK
    assert any(isinstance(r, Metric) and r.name == "lamplighter_class_alerts" and r.value == 39 for r in results)
    params = {**FILECLASS, "alerts": ("fixed", (1, 100))}
    assert worst(ab.check_lamplighter_fileclass("outsized", params, POLICIES)) == State.WARN
    params = {**FILECLASS, "bytes": ("fixed", (100 * 1024**3, 1024**4))}
    assert worst(ab.check_lamplighter_fileclass("outsized", params, POLICIES)) == State.CRIT


def test_fileclass_that_vanished_yields_nothing():
    assert list(ab.check_lamplighter_fileclass("gone", FILECLASS, POLICIES)) == []


def test_policy_status_levels_including_absent_status():
    assert worst(ab.check_lamplighter_policy("checksum", POLICY, POLICIES)) == State.OK
    params = {"status_levels": [{"status": "failed", "levels": ("fixed", (1, 10))}]}
    results = list(ab.check_lamplighter_policy("checksum", params, POLICIES))
    assert worst(results) == State.OK                            # no "failed" entries: 0
    assert any(isinstance(r, Metric) and r.name == "lamplighter_status_failed" and r.value == 0 for r in results)
    params = {"status_levels": [{"status": "alert", "levels": ("fixed", (10, 30))}]}
    assert worst(ab.check_lamplighter_policy("alert", params, POLICIES)) == State.CRIT
    names = {r.name for r in ab.check_lamplighter_policy("alert", POLICY, POLICIES) if isinstance(r, Metric)}
    assert names == {"lamplighter_status_alert", "lamplighter_status_not_set"}


def test_jobstats_service():
    off = {"configured": False}
    assert list(ab.discover_lamplighter_jobstats(off)) == []
    js = {"configured": True, "window_seconds": 300, "md_ops_per_second": 850.0, "read_bytes_per_second": 1e7,
          "write_bytes_per_second": 3e7, "sources": 3, "sources_not_reporting": 0,
          "top_md_job": {"job_id": "find.20003", "md_ops_per_second": 840.0}}
    assert worst(ab.check_lamplighter_jobstats(JOBSTATS, js)) == State.OK
    assert worst(ab.check_lamplighter_jobstats(JOBSTATS, {**js, "sources_not_reporting": 1})) == State.WARN
    params = {**JOBSTATS, "md_ops_per_second": ("fixed", (500.0, 1000.0))}
    assert worst(ab.check_lamplighter_jobstats(params, js)) == State.WARN
    assert worst(ab.check_lamplighter_jobstats(JOBSTATS, {"configured": True, "error": "OperationalError"})) == State.UNKNOWN
