# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Unit tests: ACL policy and configuration validation (no services)."""

import pytest

from lux_webui.acl import BUILTIN_ROLES, AccessPolicy, Perm
from lux_webui.config import AccessConfig, LDAPConfig, RoleMapping

ADMINS = "cn=lux-admins,ou=groups,dc=lamplighter,dc=test"
DM = "cn=lux-data-managers,ou=groups,dc=lamplighter,dc=test"
VIEW = "cn=lux-viewers,ou=groups,dc=lamplighter,dc=test"


def policy():
    return AccessPolicy(AccessConfig(
        bootstrap_admin_groups=[ADMINS],
        role_mappings=[RoleMapping(group=DM, roles=["data-manager"]),
                       RoleMapping(group=VIEW, roles=["viewer"])]))


def test_no_groups_means_no_access():
    g = policy().grants_for([])
    assert not g.has_access and not g.roles


def test_unmapped_group_means_no_access():
    assert not policy().grants_for(["cn=proj-astro,ou=groups,dc=lamplighter,dc=test"]).has_access


def test_bootstrap_group_is_admin_with_every_permission():
    g = policy().grants_for([ADMINS])
    assert g.roles == {"admin"} and g.permissions == frozenset(Perm)


def test_roles_union_across_groups():
    g = policy().grants_for([VIEW, DM])
    assert g.roles == {"viewer", "data-manager"}
    assert g.allows(Perm.COLD_EXPORT) and not g.allows(Perm.ADMIN_ACL)


def test_viewer_cannot_export_or_see_all_paths():
    g = policy().grants_for([VIEW])
    assert g.allows(Perm.DASHBOARD_VIEW)
    assert not g.allows(Perm.COLD_EXPORT) and not g.allows(Perm.PATHS_VIEW_ALL)


def test_dn_matching_ignores_case_and_spacing():
    g = policy().grants_for(["CN=LUX-Viewers, OU=Groups, DC=lamplighter, DC=test"])
    assert g.roles == {"viewer"}


def test_unknown_role_in_mapping_is_rejected():
    with pytest.raises(ValueError, match="unknown roles"):
        AccessPolicy(AccessConfig(role_mappings=[RoleMapping(group=VIEW, roles=["superuser"])]))


def test_admin_role_holds_all_permissions():
    assert BUILTIN_ROLES["admin"] == frozenset(Perm)


def _ldap(**kw):
    base = dict(servers=["ldaps://ldap.test"], bind_dn="cn=x", bind_password_file="/dev/null",
                users={"base_dn": "ou=people,dc=t"}, groups={"base_dn": "ou=groups,dc=t"})
    base.update(kw)
    return LDAPConfig.model_validate(base)


def test_plain_ldap_url_is_refused():
    with pytest.raises(ValueError, match="plain LDAP refused"):
        _ldap(servers=["ldap://ldap.test"])


def test_plain_ldap_allowed_with_starttls():
    assert _ldap(servers=["ldap://ldap.test"], starttls=True).starttls


@pytest.mark.parametrize("dtype, login, ufilter, gfilter, membership", [
    ("rfc2307bis", "uid", "(&(objectClass=posixAccount)(uid={username}))",
     "(objectClass=groupOfNames)", "memberof"),
    ("rfc2307", "uid", "(&(objectClass=posixAccount)(uid={username}))",
     "(objectClass=posixGroup)", "member_search"),
    ("active_directory", "sAMAccountName", "(&(objectClass=user)(sAMAccountName={username}))",
     "(objectClass=group)", "memberof"),
])
def test_directory_type_defaults(dtype, login, ufilter, gfilter, membership):
    c = _ldap(directory_type=dtype)
    assert (c.users.login_attr, c.users.filter, c.groups.filter, c.groups.membership) == \
           (login, ufilter, gfilter, membership)
