# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Access control: permissions, grouped into roles, assigned to LDAP groups.

A user's effective permissions are the union over the roles of every
LDAP group they belong to. A user whose groups map to no role has no
access at all. Permissions are enforced in the API on every request;
the front end only hides what a user cannot use.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import AccessConfig
from .ldapdir import norm_dn


class Perm(str, Enum):
    DASHBOARD_VIEW = "dashboard.view"
    USAGE_VIEW_AGGREGATES = "usage.view_aggregates"
    USAGE_VIEW_ALL_USERS = "usage.view_all_users"
    PATHS_VIEW_OWN = "paths.view_own"
    PATHS_VIEW_ALL = "paths.view_all"
    COLD_VIEW = "cold.view"
    COLD_EXPORT = "cold.export"
    TRIAGE_VIEW = "triage.view"
    COLLECTOR_STATUS_VIEW = "collector.status.view"
    ADMIN_LDAP = "admin.ldap"
    ADMIN_ACL = "admin.acl"
    ADMIN_AUDIT_VIEW = "admin.audit.view"


P = Perm
BUILTIN_ROLES: dict[str, frozenset[Perm]] = {
    "user": frozenset({P.DASHBOARD_VIEW, P.PATHS_VIEW_OWN}),
    "viewer": frozenset({P.DASHBOARD_VIEW, P.USAGE_VIEW_AGGREGATES, P.PATHS_VIEW_OWN}),
    "data-manager": frozenset({
        P.DASHBOARD_VIEW, P.USAGE_VIEW_AGGREGATES, P.USAGE_VIEW_ALL_USERS,
        P.PATHS_VIEW_OWN, P.PATHS_VIEW_ALL, P.COLD_VIEW, P.COLD_EXPORT,
        P.COLLECTOR_STATUS_VIEW}),
    "admin": frozenset(Perm),
}


@dataclass(frozen=True)
class Grants:
    roles: frozenset[str]
    permissions: frozenset[Perm]

    def allows(self, perm: Perm) -> bool:
        return perm in self.permissions

    @property
    def has_access(self) -> bool:
        return bool(self.permissions)


class AccessPolicy:
    def __init__(self, cfg: AccessConfig, roles: dict[str, frozenset[Perm]] | None = None):
        self.roles = dict(roles or BUILTIN_ROLES)
        self.bootstrap = {norm_dn(g) for g in cfg.bootstrap_admin_groups}
        self.mapping: dict[str, set[str]] = {}
        for m in cfg.role_mappings:
            unknown = set(m.roles) - self.roles.keys()
            if unknown:
                raise ValueError(f"role mapping for {m.group}: unknown roles {sorted(unknown)}")
            self.mapping.setdefault(norm_dn(m.group), set()).update(m.roles)

    def grants_for(self, group_dns: tuple[str, ...] | list[str]) -> Grants:
        groups = {norm_dn(g) for g in group_dns}
        roles: set[str] = set()
        if groups & self.bootstrap:
            roles.add("admin")
        for g in groups:
            roles |= self.mapping.get(g, set())
        perms: set[Perm] = set()
        for r in roles:
            perms |= self.roles[r]
        return Grants(roles=frozenset(roles), permissions=frozenset(perms))
