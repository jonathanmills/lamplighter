# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""LDAP diagnostics for the Directory admin page.

Runs the same connection the UI uses, step by step, for each server,
and explains failures in plain words. Never returns secrets.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import ldap
from cryptography import x509

from .config import LDAPConfig, read_secret
from .ldapdir import Directory, norm_dn


def _ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


def _explain(exc: ldap.LDAPError) -> tuple[str, str]:
    """(step that failed, plain explanation) for an LDAP exception."""
    info = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
    text = " ".join(str(info.get(k, "")) for k in ("desc", "info")).strip() or str(exc)
    low = text.lower()
    if "certificate verify failed" in low or "certificate" in low:
        return "tls", f"The server's certificate is not trusted: {text}. Check ca_file."
    if isinstance(exc, (ldap.SERVER_DOWN, ldap.CONNECT_ERROR)):
        return "tls", f"Cannot reach the server or complete TLS: {text}."
    if isinstance(exc, ldap.INVALID_CREDENTIALS):
        return "bind", "The service account's DN or password was refused. Check bind_dn and bind_password_file."
    if isinstance(exc, ldap.CONFIDENTIALITY_REQUIRED):
        return "bind", "The server requires an encrypted connection. Use ldaps:// or StartTLS."
    if isinstance(exc, ldap.NO_SUCH_OBJECT):
        return "search", f"The base DN does not exist: {text}."
    return "other", text


def _cert_info(der: bytes | None) -> dict | None:
    if not der:
        return None
    cert = x509.load_der_x509_certificate(der)
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = sans.get_values_for_type(x509.DNSName) + [str(i) for i in sans.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        names = []
    expires = cert.not_valid_after_utc
    return {"subject": cert.subject.rfc4514_string(), "issuer": cert.issuer.rfc4514_string(),
            "expires": int(expires.timestamp()),
            "days_left": (expires - datetime.now(timezone.utc)).days, "names": names}


def test_servers(directory: Directory) -> list[dict]:
    """Each configured server, step by step: TLS, certificate, bind, searches."""
    cfg: LDAPConfig = directory.cfg
    results = []
    for url in cfg.servers:
        steps: list[dict] = []
        single = cfg.model_copy(update={"servers": [url]})
        d = Directory(single)
        t0 = time.monotonic()
        conn = None
        try:
            conn = d._connect()
            conn.simple_bind_s(cfg.bind_dn, read_secret(cfg.bind_password_file))
            transport = "StartTLS" if url.startswith("ldap://") and cfg.starttls else (
                "LDAPS" if url.startswith("ldaps://") else "plain (insecure)")
            steps.append({"step": "Connect and TLS", "ok": True, "ms": _ms(t0), "detail": transport})
            try:
                cert = _cert_info(conn.get_option(ldap.OPT_X_TLS_PEERCERT))
            except (ldap.LDAPError, ValueError):
                cert = None
            if cert:
                ok = cert["days_left"] > 0
                steps.append({"step": "Server certificate", "ok": ok, "ms": 0,
                              "detail": f"{cert['subject']}, issued by {cert['issuer']}, "
                                        f"expires in {cert['days_left']} days", "cert": cert})
            steps.append({"step": "Service bind", "ok": True, "ms": _ms(t0), "detail": conn.whoami_s()})
            for label, base, scope, flt in (
                ("Search users", cfg.users.base_dn, cfg.users.scope, cfg.users.filter.replace("{username}", "*")),
                ("Search groups", cfg.groups.base_dn, cfg.groups.scope, cfg.groups.filter),
            ):
                t1 = time.monotonic()
                n = sum(1 for _ in Directory._paged(conn, base, ldap.SCOPE_SUBTREE if scope == "sub"
                                                    else ldap.SCOPE_ONELEVEL, flt, ["1.1"]))
                steps.append({"step": label, "ok": n > 0, "ms": _ms(t1),
                              "detail": f"{n} entries under {base} matching {flt}"
                                        + ("" if n else " (none found: check the base DN and filter)")})
        except ldap.LDAPError as exc:
            where, why = _explain(exc)
            name = {"tls": "Connect and TLS", "bind": "Service bind", "search": "Search"}.get(where, "LDAP")
            steps.append({"step": name, "ok": False, "ms": _ms(t0), "detail": why})
        finally:
            if conn is not None:
                try:
                    conn.unbind_s()
                except ldap.LDAPError:
                    pass
        results.append({"server": url, "ok": all(s["ok"] for s in steps), "steps": steps})
    return results


def check_groups(directory: Directory, dns: list[str]) -> dict[str, dict]:
    """Does each configured group DN exist, and how many members does it have?"""
    out: dict[str, dict] = {}
    conn = directory._service_conn()
    try:
        for dn in dns:
            try:
                res = conn.search_s(dn, ldap.SCOPE_BASE, "(objectClass=*)", ["member", "memberUid", "cn"])
                entry = res[0][1] if res else {}
                members = len(entry.get("member", [])) or len(entry.get("memberUid", []))
                out[norm_dn(dn)] = {"exists": True, "members": members}
            except ldap.NO_SUCH_OBJECT:
                out[norm_dn(dn)] = {"exists": False, "members": 0}
    finally:
        conn.unbind_s()
    return out
