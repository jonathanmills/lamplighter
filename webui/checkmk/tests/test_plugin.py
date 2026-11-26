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

from cmk_addons.plugins.robinhood.agent_based import robinhood as ab
from cmk_addons.plugins.robinhood.rulesets import robinhood as rs
from cmk_addons.plugins.robinhood.server_side_calls import robinhood as ssc
from cmk_addons.plugins.robinhood.special_agent import agent_robinhood as agent

STATUS = ab.check_plugin_robinhood_status.check_default_parameters
COLLECTOR = ab.check_plugin_robinhood_collector.check_default_parameters
USAGE = ab.check_plugin_robinhood_usage.check_default_parameters


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
                         "--token-url", "https://keycloak.test:9443/realms/rbh/protocol/openid-connect/token",
                         "--client-id", "rbh-checkmk", "--client-secret", "dev-checkmk-client-secret",
                         "--ca-file", "/certs/ca.pem"])
    assert rc == 0
    return buf.getvalue()


def test_agent_prints_three_json_sections(agent_output):
    for name in ("robinhood_status", "robinhood_collector", "robinhood_usage"):
        data = json.loads(section_lines(agent_output, name)[0][0])
        assert isinstance(data, dict)


def test_live_data_gives_ok_services(agent_output):
    status = ab.parse_json(section_lines(agent_output, "robinhood_status"))
    collector = ab.parse_json(section_lines(agent_output, "robinhood_collector"))
    usage = ab.parse_json(section_lines(agent_output, "robinhood_usage"))
    for plugin, sec, params in ((ab.check_robinhood_status, status, STATUS),
                                (ab.check_robinhood_collector, collector, COLLECTOR),
                                (ab.check_robinhood_usage, usage, USAGE)):
        results = list(plugin(params, sec))
        assert results and worst(results) is State.OK, results
    metrics = {m.name for m in ab.check_robinhood_usage(USAGE, usage) if isinstance(m, Metric)}
    assert {"robinhood_bytes", "robinhood_files", "robinhood_cold_1y_bytes"} <= metrics


def test_agent_fails_cleanly_on_a_wrong_secret(capsys):
    rc = agent.main(["--url", "https://webui.test:8443",
                     "--token-url", "https://keycloak.test:9443/realms/rbh/protocol/openid-connect/token",
                     "--client-id", "rbh-checkmk", "--client-secret", "wrong", "--ca-file", "/certs/ca.pem"])
    assert rc == 1 and "HTTP 401" in capsys.readouterr().err


def test_agent_refuses_an_untrusted_certificate(capsys):
    rc = agent.main(["--url", "https://webui.test:8443",
                     "--token-url", "https://keycloak.test:9443/realms/rbh/protocol/openid-connect/token",
                     "--client-id", "rbh-checkmk", "--client-secret", "dev-checkmk-client-secret"])
    assert rc == 1 and "CERTIFICATE_VERIFY_FAILED" in capsys.readouterr().err


# --- checks on hand-made sections ----------------------------------------------

def test_discovery_is_one_service():
    assert list(ab.discover_single({})) == [Service()]


def test_database_down_is_crit():
    assert worst(ab.check_robinhood_status(STATUS, {"database": {"reachable": False, "error": "OperationalError"}})) is State.CRIT


@pytest.mark.parametrize("age_days, state", [(1, State.OK), (6, State.WARN), (9, State.CRIT)])
def test_scan_age_levels(age_days, state):
    sec = {"database": {"reachable": True}, "last_scan": {"status": "done", "age_seconds": age_days * 86400, "errors": 0}}
    assert worst(ab.check_robinhood_status(STATUS, sec)) is state


def test_changelog_backlog_levels():
    sec = {"database": {"reachable": True}, "last_scan": {"status": "done", "age_seconds": 60, "errors": 0},
           "changelog": {"backlog_records": 2_000_000}}
    assert worst(ab.check_robinhood_status(STATUS, sec)) is State.WARN


def test_collector_failed_and_stale():
    old = time.time() - 60 * 3600
    sec = {"last_run": {"status": "failed", "error": "TimeoutError"}, "last_ok_finished": old}
    results = list(ab.check_robinhood_collector(COLLECTOR, sec))
    assert worst(results) is State.CRIT
    assert any("failed" in r.summary for r in results if isinstance(r, Result))


def test_collector_never_ran():
    assert worst(ab.check_robinhood_collector(COLLECTOR, {})) is State.WARN


def test_cold_data_levels_when_configured():
    tib = 1024**4
    sec = {"usage": {"by_type": {"file": {"entries": 10, "bytes": 600 * tib}}},
           "age": {"bands": [{"band": 4, "bytes": 200 * tib}, {"band": 5, "bytes": 400 * tib}]}}
    params = {**USAGE, "cold_1y_bytes": ("fixed", (100 * tib, 500 * tib))}
    assert worst(ab.check_robinhood_usage(params, sec)) is State.CRIT
    assert worst(ab.check_robinhood_usage(USAGE, sec)) is State.OK      # no levels by default


def test_bad_agent_output_is_ignored():
    assert ab.parse_json([["not json"]]) is None and ab.parse_json([]) is None


# --- server-side call and rulesets ---------------------------------------------

def test_secret_is_passed_by_reference_only():
    host = HostConfig(name="robinhood", ipv4_config=IPv4Config(address="10.0.0.1"))
    params = {"url": "https://rbh.example.org", "token_url": "https://kc.example.org/t",
              "client_id": "rbh-checkmk", "client_secret": Secret(42), "timeout": 20}
    [cmd] = list(ssc.special_agent_robinhood(params, host))
    args = cmd.command_arguments
    i = args.index("--client-secret-id")
    assert isinstance(args[i + 1], Secret) and args[i + 1].pass_safely
    assert "--client-secret" not in args


def test_rulesets_build():
    for spec in (rs.rule_spec_special_agent_robinhood, rs.rule_spec_robinhood_status,
                 rs.rule_spec_robinhood_collector, rs.rule_spec_robinhood_usage):
        form = spec.parameter_form()
        assert form.elements
    assert set(rs._status_form().elements) == set(STATUS)
    assert set(rs._collector_form().elements) == set(COLLECTOR)
    assert set(rs._usage_form().elements) == set(USAGE)
