#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Special agent for the Lamplighter web UI's monitoring API.

Runs on the Checkmk server (Checkmk polls Lamplighter; nothing is
installed on the Lamplighter host):

1. Get a short-lived access token from Keycloak with the OAuth 2.0
   client-credentials grant. The client secret comes from Checkmk's
   password store and never appears on the command line.
2. GET /api/v1/monitoring with that token.
3. Print three agent sections of JSON for the check plugins.

Only the Python standard library is used, plus Checkmk's password
store API.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence

from cmk.password_store.v1_unstable import parser_add_secret_option, resolve_secret_option

SECRET_OPTION = "client-secret"


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="agent_lamplighter", description=__doc__.split("\n\n")[0])
    p.add_argument("--url", required=True, help="base URL of the Lamplighter web UI, e.g. https://lamplighter.example.org")
    p.add_argument("--token-url", required=True,
                   help="token endpoint, e.g. https://kc.example.org/realms/R/protocol/openid-connect/token")
    p.add_argument("--client-id", required=True)
    parser_add_secret_option(p, long=f"--{SECRET_OPTION}", help="client secret (debugging only)", required=True)
    p.add_argument("--ca-file", help="CA bundle to verify both servers (default: system trust store)")
    p.add_argument("--no-cert-check", action="store_true", help="do not verify TLS certificates (not recommended)")
    p.add_argument("--timeout", type=float, default=30.0)
    return p.parse_args(argv)


def _context(args: argparse.Namespace) -> ssl.SSLContext:
    if args.no_cert_check:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=args.ca_file)


def _request(req: urllib.request.Request, ctx: ssl.SSLContext, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{req.full_url}: HTTP {exc.code} {exc.reason}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{req.full_url}: {exc.reason}") from exc


def fetch(args: argparse.Namespace, secret: str) -> dict:
    ctx = _context(args)
    form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": args.client_id,
                                   "client_secret": secret}).encode()
    token = _request(urllib.request.Request(args.token_url, data=form, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}),
        ctx, args.timeout).get("access_token")
    if not token:
        raise RuntimeError("the token endpoint returned no access_token")
    return _request(urllib.request.Request(args.url.rstrip("/") + "/api/v1/monitoring", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json"}), ctx, args.timeout)


def sections(body: dict) -> str:
    """Agent output: one JSON document per section, on a single line."""
    out = []
    status = dict(body.get("lamplighter") or {})
    status["database"] = body.get("database", {})
    out.append("<<<lamplighter_status:sep(0)>>>")
    out.append(json.dumps(status, sort_keys=True))
    out.append("<<<lamplighter_collector:sep(0)>>>")
    out.append(json.dumps(body.get("collector") or {}, sort_keys=True))
    out.append("<<<lamplighter_usage:sep(0)>>>")
    out.append(json.dumps({"usage": body.get("usage") or {},
                           "age": (body.get("collector") or {}).get("age_by_access")}, sort_keys=True))
    return "\n".join(out) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        body = fetch(args, resolve_secret_option(args, SECRET_OPTION).reveal())
    except Exception as exc:  # Checkmk shows this message on the agent service
        sys.stderr.write(f"Lamplighter monitoring API: {exc}\n")
        return 1
    sys.stdout.write(sections(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
