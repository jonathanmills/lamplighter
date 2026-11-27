#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Server-side call: turns the special agent rule into agent_lamplighter's
command line. The client secret is passed as a password-store reference
(--client-secret-id), never as plain text."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from cmk.server_side_calls.v1 import (
    HostConfig,
    noop_parser,
    replace_macros,
    Secret,
    SpecialAgentCommand,
    SpecialAgentConfig,
)


def _commands(params: Mapping[str, object], host_config: HostConfig) -> Iterable[SpecialAgentCommand]:
    secret = params["client_secret"]
    assert isinstance(secret, Secret)
    args: list[str | Secret] = [
        "--url", replace_macros(str(params["url"]), host_config.macros),
        "--token-url", replace_macros(str(params["token_url"]), host_config.macros),
        "--client-id", str(params["client_id"]),
        "--client-secret-id", secret,
    ]
    if params.get("ca_file"):
        args += ["--ca-file", str(params["ca_file"])]
    if params.get("no_cert_check"):
        args.append("--no-cert-check")
    if "timeout" in params:
        args += ["--timeout", str(params["timeout"])]
    yield SpecialAgentCommand(command_arguments=args)


special_agent_lamplighter = SpecialAgentConfig(
    name="lamplighter",
    parameter_parser=noop_parser,
    commands_function=_commands,
)
