#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Rulesets (Checkmk rulesets API v1): the special agent connection, and
the thresholds for the Lamplighter services. Lamplighter reports facts
only; every threshold is set here."""

from __future__ import annotations

from cmk.rulesets.v1 import Help, Title
from cmk.rulesets.v1.form_specs import (
    BooleanChoice,
    DataSize,
    DefaultValue,
    DictElement,
    Dictionary,
    Float,
    IECMagnitude,
    Integer,
    LevelDirection,
    List,
    Password,
    SimpleLevels,
    String,
    TimeMagnitude,
    TimeSpan,
)
from cmk.rulesets.v1.form_specs.validators import LengthInRange, Url, UrlProtocol
from cmk.rulesets.v1.rule_specs import CheckParameters, HostAndItemCondition, HostCondition, SpecialAgent, Topic

_HTTPS = Url(protocols=[UrlProtocol.HTTPS])


# --- special agent ------------------------------------------------------------

def _agent_form() -> Dictionary:
    return Dictionary(
        title=Title("Lamplighter"),
        help_text=Help(
            "Polls the Lamplighter web UI's monitoring API. The agent gets a short-lived token "
            "from Keycloak with the client-credentials grant, so create a confidential client "
            "with a service account that holds the web UI's 'monitoring' client role."),
        elements={
            "url": DictElement(required=True, parameter_form=String(
                title=Title("Lamplighter web UI URL"),
                help_text=Help("For example https://lamplighter.example.org"),
                custom_validate=(_HTTPS,))),
            "token_url": DictElement(required=True, parameter_form=String(
                title=Title("Keycloak token endpoint"),
                help_text=Help("For example https://keycloak.example.org/realms/REALM/protocol/openid-connect/token"),
                custom_validate=(_HTTPS,))),
            "client_id": DictElement(required=True, parameter_form=String(
                title=Title("Client ID"), prefill=DefaultValue("lux-checkmk"),
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


rule_spec_special_agent_lamplighter = SpecialAgent(
    name="lamplighter",
    title=Title("Lamplighter"),
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


def _optional_levels(title: str, template, warn, crit) -> SimpleLevels:
    """Levels a site may set; the check's default is no levels. warn/crit
    only prefill the form when an admin switches levels on."""
    return SimpleLevels(title=Title(title), form_spec_template=template,
                        level_direction=LevelDirection.UPPER, prefill_fixed_levels=DefaultValue((warn, crit)))


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


rule_spec_lamplighter_status = CheckParameters(
    name="lamplighter_status", title=Title("Lamplighter status"), topic=Topic.STORAGE,
    parameter_form=_status_form, condition=HostCondition())

rule_spec_lamplighter_collector = CheckParameters(
    name="lamplighter_collector", title=Title("Lamplighter collector"), topic=Topic.STORAGE,
    parameter_form=_collector_form, condition=HostCondition())

rule_spec_lamplighter_usage = CheckParameters(
    name="lamplighter_usage", title=Title("Lamplighter usage"), topic=Topic.STORAGE,
    parameter_form=_usage_form, condition=HostCondition())


def _fileclass_form() -> Dictionary:
    return Dictionary(
        help_text=Help("Thresholds for one Robinhood fileclass. Use the rule's service condition "
                       "to choose fileclasses. Robinhood alert policies only classify entries; "
                       "decide here how many marked entries matter."),
        elements={
            "entries": DictElement(parameter_form=_optional_levels("Entries in the fileclass", Integer(), 1_000_000, 10_000_000)),
            "bytes": DictElement(parameter_form=_optional_levels(
                "Volume of the fileclass", DataSize(displayed_magnitudes=[IECMagnitude.GIBI, IECMagnitude.TEBI,
                                                                          IECMagnitude.PEBI]),
                100 * 1024**4, 500 * 1024**4)),
            "alerts": DictElement(parameter_form=_optional_levels(
                "Entries marked 'alert' by a classifier alert policy", Integer(), 1, 100)),
        })


def _policy_form() -> Dictionary:
    return Dictionary(
        help_text=Help("Thresholds on the number of entries in a given status of one Robinhood "
                       "policy, for example 'failed' for a checksum policy or 'alert' for an "
                       "alert policy used as a classifier."),
        elements={
            "status_levels": DictElement(parameter_form=List(
                title=Title("Levels per status"),
                element_template=Dictionary(elements={
                    "status": DictElement(required=True, parameter_form=String(
                        title=Title("Status value (as stored by Robinhood, e.g. failed, alert, new)"),
                        custom_validate=(LengthInRange(min_value=1),))),
                    "levels": DictElement(required=True, parameter_form=_count_levels(
                        "Entries in this status", 1, 100)),
                }))),
        })


def _jobstats_form() -> Dictionary:
    return Dictionary(elements={
        "md_ops_per_second": DictElement(parameter_form=_optional_levels(
            "Metadata requests per second (all jobs)", Float(), 20_000.0, 50_000.0)),
        "read_bytes_per_second": DictElement(parameter_form=_optional_levels(
            "Data read per second, in bytes (all jobs)", Float(), 10.0 * 1024**3, 20.0 * 1024**3)),
        "write_bytes_per_second": DictElement(parameter_form=_optional_levels(
            "Data written per second, in bytes (all jobs)", Float(), 10.0 * 1024**3, 20.0 * 1024**3)),
        "sources_not_reporting": DictElement(parameter_form=_count_levels(
            "Jobstats sources (servers) not reporting", 1, 3)),
    })


rule_spec_lamplighter_fileclass = CheckParameters(
    name="lamplighter_fileclass", title=Title("Lamplighter fileclass"), topic=Topic.STORAGE,
    parameter_form=_fileclass_form, condition=HostAndItemCondition(item_title=Title("Fileclass")))

rule_spec_lamplighter_policy = CheckParameters(
    name="lamplighter_policy", title=Title("Lamplighter policy"), topic=Topic.STORAGE,
    parameter_form=_policy_form, condition=HostAndItemCondition(item_title=Title("Policy")))

rule_spec_lamplighter_jobstats = CheckParameters(
    name="lamplighter_jobstats", title=Title("Lamplighter jobstats"), topic=Topic.STORAGE,
    parameter_form=_jobstats_form, condition=HostCondition())
