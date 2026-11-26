# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Configuration, loaded from one YAML file.

The file is named by the RBH_WEBUI_CONFIG environment variable
(default /etc/rbh-webui/config.yaml). Secrets never appear in it:
each one is read from its own file (``*_file`` settings), which
should be readable only by the service account.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


def read_secret(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


class ServerConfig(BaseModel):
    base_url: str = Field(description="Public URL of the UI, e.g. https://rbh.example.org")
    session_secret_file: str
    session_ttl_seconds: int = 8 * 3600
    # Lax (not Strict): the OIDC callback is a cross-site top-level
    # navigation from the identity provider, and must carry the cookie.
    cookie_samesite: Literal["lax", "strict"] = "lax"


class OIDCConfig(BaseModel):
    enabled: bool = True
    issuer: str
    client_id: str
    client_secret_file: str
    ca_file: str | None = None
    scope: str = "openid profile email"
    username_claim: str = "preferred_username"


class DirectoryType(str, Enum):
    rfc2307bis = "rfc2307bis"
    rfc2307 = "rfc2307"
    active_directory = "active_directory"


class LDAPUsers(BaseModel):
    base_dn: str
    scope: Literal["sub", "one"] = "sub"
    filter: str | None = None          # {username} is substituted (escaped)
    login_attr: str | None = None
    name_attr: str = "cn"
    mail_attr: str = "mail"
    uidnumber_attr: str = "uidNumber"
    gidnumber_attr: str = "gidNumber"


class LDAPGroups(BaseModel):
    base_dn: str
    scope: Literal["sub", "one"] = "sub"
    filter: str | None = None
    # "memberof": read the user's memberOf attribute (RFC 2307bis, AD).
    # "member_search": search groups whose member (or memberUid) names the user.
    membership: Literal["memberof", "member_search"] | None = None
    nested: bool = False


class LDAPConfig(BaseModel):
    servers: list[str]
    starttls: bool = False             # for ldap:// URLs; ldaps:// is always TLS
    ca_file: str | None = None
    allow_insecure: bool = False       # permit plain ldap:// (tests only)
    bind_dn: str
    bind_password_file: str
    directory_type: DirectoryType = DirectoryType.rfc2307bis
    timeout_seconds: float = 5.0
    cache_ttl_seconds: int = 300
    users: LDAPUsers
    groups: LDAPGroups

    @model_validator(mode="after")
    def _defaults_for_type(self) -> "LDAPConfig":
        t = self.directory_type
        u, g = self.users, self.groups
        if u.login_attr is None:
            u.login_attr = "sAMAccountName" if t is DirectoryType.active_directory else "uid"
        if u.filter is None:
            u.filter = (f"(&(objectClass=user)({u.login_attr}={{username}}))"
                        if t is DirectoryType.active_directory
                        else f"(&(objectClass=posixAccount)({u.login_attr}={{username}}))")
        if g.filter is None:
            g.filter = {"rfc2307bis": "(objectClass=groupOfNames)",
                        "rfc2307": "(objectClass=posixGroup)",
                        "active_directory": "(objectClass=group)"}[t.value]
        if g.membership is None:
            g.membership = "member_search" if t is DirectoryType.rfc2307 else "memberof"
        for url in self.servers:
            if url.startswith("ldap://") and not (self.starttls or self.allow_insecure):
                raise ValueError(f"{url}: plain LDAP refused; use ldaps:// or starttls: true")
        return self


class LoginConfig(BaseModel):
    ldap_bind_enabled: bool = False    # direct LDAP password login (form)


class RoleMapping(BaseModel):
    group: str                         # LDAP group DN
    roles: list[str]


class AccessConfig(BaseModel):
    # Bootstrap: members of these groups are admins regardless of the
    # role mappings below, so the first login can set up the rest.
    bootstrap_admin_groups: list[str] = []
    role_mappings: list[RoleMapping] = []


class DatabaseConfig(BaseModel):
    host: str
    port: int = 3306
    name: str
    user: str
    password_file: str


class MonitoringConfig(BaseModel):
    enabled: bool = True
    audience: str                      # expected "aud" in machine tokens
    required_role: str = "monitoring"  # client role on the audience client


class Settings(BaseModel):
    server: ServerConfig
    oidc: OIDCConfig | None = None
    ldap: LDAPConfig
    login: LoginConfig = LoginConfig()
    access: AccessConfig = AccessConfig()
    database: DatabaseConfig
    monitoring: MonitoringConfig | None = None

    @model_validator(mode="after")
    def _need_a_login_method(self) -> "Settings":
        if not ((self.oidc and self.oidc.enabled) or self.login.ldap_bind_enabled):
            raise ValueError("enable at least one login method: oidc or login.ldap_bind_enabled")
        return self


def load_settings(path: str | None = None) -> Settings:
    path = path or os.environ.get("RBH_WEBUI_CONFIG", "/etc/rbh-webui/config.yaml")
    with open(path, encoding="utf-8") as fh:
        return Settings.model_validate(yaml.safe_load(fh))
