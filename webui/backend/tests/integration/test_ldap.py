# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Directory lookups and password checks against the dev OpenLDAP."""

import pytest

from lux_webui.ldapdir import Directory, DirectoryError

pytestmark = pytest.mark.integration


def test_find_user_with_memberof_groups(directory):
    u = directory.find_user("alice")
    assert (u.uid, u.uid_number, u.gid_number, u.mail) == ("alice", 20001, 30001, "alice@lamplighter.test")
    assert set(u.groups) == {"cn=proj-astro,ou=groups,dc=lamplighter,dc=test",
                             "cn=lux-viewers,ou=groups,dc=lamplighter,dc=test"}


def test_member_search_gives_the_same_groups(settings):
    cfg = settings.ldap.model_copy(deep=True)
    cfg.groups.membership = "member_search"
    assert set(Directory(cfg).find_user("bob").groups) == {
        "cn=proj-astro,ou=groups,dc=lamplighter,dc=test", "cn=proj-climate,ou=groups,dc=lamplighter,dc=test"}


def test_unknown_user(directory):
    assert directory.find_user("nobody") is None


@pytest.mark.parametrize("evil", ["*", "alice)(uid=*", "a*", "alice\\"])
def test_filter_injection_finds_nothing(directory, evil):
    assert directory.find_user(evil) is None


def test_authenticate_good_password(directory):
    assert directory.authenticate("carol", "password").uid == "carol"


@pytest.mark.parametrize("user, pw", [("carol", "wrong"), ("carol", ""), ("", "password"),
                                      ("nobody", "password")])
def test_authenticate_refuses(directory, user, pw):
    assert directory.authenticate(user, pw) is None


def test_untrusted_ca_is_rejected(settings, tmp_path):
    import subprocess
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=Unrelated CA", "-keyout", str(tmp_path / "k.pem"),
                    "-out", str(tmp_path / "ca.pem")], check=True, capture_output=True)
    cfg = settings.ldap.model_copy(deep=True)
    cfg.ca_file = str(tmp_path / "ca.pem")
    with pytest.raises(DirectoryError):
        Directory(cfg).find_user("alice", refresh=True)


def test_wrong_service_password_is_reported(settings, tmp_path):
    (tmp_path / "pw").write_text("wrong\n")
    cfg = settings.ldap.model_copy(deep=True)
    cfg.bind_password_file = str(tmp_path / "pw")
    with pytest.raises(DirectoryError, match="service bind failed"):
        Directory(cfg).find_user("alice", refresh=True)


def test_connection_diagnostics(directory):
    r = directory.test_connection("dave")
    assert r["bind"] == "ok" and "cn=lux-admins,ou=groups,dc=lamplighter,dc=test" in r["sample_user"]["groups"]
