#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Rulesets (Checkmk rulesets API v1): the special agent connection, and
the thresholds for the three Robinhood services."""

from __future__ import annotations

from cmk.rulesets.v1 import Help, Title
from cmk.rulesets.v1.form_specs import (
    BooleanChoice,
    DataSize,
    DefaultValue,
    DictElement,
    Dictionary,
    IECMagnitude,
    Integer,
    LevelDirection,
    Password,
    SimpleLevels,
    String,
    TimeMagnitude,
    TimeSpan,
)
from cmk.rulesets.v1.form_specs.validators import LengthInRange, Url, UrlProtocol
from cmk.rulesets.v1.rule_specs import CheckParameters, HostCondition, SpecialAgent, Topic

_HTTPS = Url(protocols=[UrlProtocol.HTTPS])


# --- special agent ------------------------------------------------------------

def _agent_form() -> Dictionary:
    return Dictionary(
        title=Title("Robinhood Policy Engine"),
        help_text=Help(
            "Polls the Robinhood web UI's monitoring API. The agent gets a short-lived token "
            "from Keycloak with the client-credentials grant, so create a confidential client "
            "with a service account that holds the web UI's 'monitoring' client role."),
        elements={
            "url": DictElement(required=True, parameter_form=String(
                title=Title("Robinhood web UI URL"),
                help_text=Help("For example https://robinhood.example.org"),
                custom_validate=(_HTTPS,))),
            "token_url": DictElement(required=True, parameter_form=String(
                title=Title("Keycloak token endpoint"),
                help_text=Help("For example https://keycloak.example.org/realms/REALM/protocol/openid-connect/token"),
                custom_validate=(_HTTPS,))),
            "client_id": DictElement(required=True, parameter_form=String(
                title=Title("Client ID"), prefill=DefaultValue("rbh-checkmk"),
                custom_validate=(LengthInRange(min_value=1),))),
            "client_secret": DictElement(required=True, parameter_form=Password(
                title=Title("Client secret"))),
            "ca_file": DictElement(required=False, parameter_form=String(
                title=Title("CA bundle file"),
                help_text=Help("Path on the Checkmk server to the CA certificates that sign the web UI "
                               "and Keycloak certificates. Leave unset to use the system trust store."))),
            "no_cert_check": DictElement(required=False, parameter_form=BooleanChoice(
                title=Title("Disable certificate verification (not recommended)"))),
            "timeout": DictElement(required=False, parameter_form=Integer(
                title=Title("Timeout (seconds)"), prefill=DefaultValue(30))),
        },
    )


rule_spec_special_agent_robinhood = SpecialAgent(
    name="robinhood",
    title=Title("Robinhood Policy Engine"),
    topic=Topic.STORAGE,
    parameter_form=_agent_form,
)


# --- thresholds ---------------------------------------------------------------

def _time_levels(title: str, warn: float, crit: float) -> SimpleLevels[float]:
    return SimpleLevels(
        title=Title(title),  # noqa: localization of plug-in strings is optional
        form_spec_template=TimeSpan(displayed_magnitudes=[TimeMagnitude.DAY, TimeMagnitude.HOUR,
                                                          TimeMagnitude.MINUTE]),
        level_direction=LevelDirection.UPPER,
        prefill_fixed_levels=DefaultValue((warn, crit)),
    )


def _count_levels(title: str, warn: int, crit: int) -> SimpleLevels[int]:
    return SimpleLevels(
        title=Title(title),
        form_spec_template=Integer(),
        level_direction=LevelDirection.UPPER,
        prefill_fixed_levels=DefaultValue((warn, crit)),
    )


def _size_levels(title: str) -> SimpleLevels[int]:
    return SimpleLevels(
        title=Title(title),
        form_spec_template=DataSize(displayed_magnitudes=[IECMagnitude.GIBI, IECMagnitude.TEBI,
                                                          IECMagnitude.PEBI]),
        level_direction=LevelDirection.UPPER,
        prefill_fixed_levels=DefaultValue((100 * 1024**4, 500 * 1024**4)),
    )


def _status_form() -> Dictionary:
    return Dictionary(elements={
        "scan_age": DictElement(parameter_form=_time_levels("Time since the last scan", 5 * 86400.0, 8 * 86400.0)),
        "scan_errors": DictElement(parameter_form=_count_levels("Errors in the last scan", 1, 1000)),
        "changelog_backlog": DictElement(parameter_form=_count_levels(
            "Changelog backlog (records read but not yet cleared)", 1_000_000, 10_000_000)),
    })


def _collector_form() -> Dictionary:
    return Dictionary(elements={
        "collector_age": DictElement(parameter_form=_time_levels(
            "Time since the last successful collector run", 26 * 3600.0, 50 * 3600.0)),
    })


def _usage_form() -> Dictionary:
    return Dictionary(elements={
        "cold_1y_bytes": DictElement(parameter_form=_size_levels("Data not read in over 1 year")),
        "cold_3y_bytes": DictElement(parameter_form=_size_levels("Data not read in over 3 years")),
    })


rule_spec_robinhood_status = CheckParameters(
    name="robinhood_status", title=Title("Robinhood status"), topic=Topic.STORAGE,
    parameter_form=_status_form, condition=HostCondition())

rule_spec_robinhood_collector = CheckParameters(
    name="robinhood_collector", title=Title("Robinhood collector"), topic=Topic.STORAGE,
    parameter_form=_collector_form, condition=HostCondition())

rule_spec_robinhood_usage = CheckParameters(
    name="robinhood_usage", title=Title("Robinhood usage"), topic=Topic.STORAGE,
    parameter_form=_usage_form, condition=HostCondition())
