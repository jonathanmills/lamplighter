# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Read people and Unix groups from the dev LDAP data file.

data.ldif is the single source of truth for test identities, so the
LDAP directory, the workbench's Unix accounts and the owners of the
generated files always agree. Only the simple LDIF used there is
supported: no line folding, no base64 values.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Person:
    uid: str
    uid_number: int
    gid_number: int
    name: str


@dataclass
class UnixGroup:
    cn: str
    gid_number: int
    members: list[str] = field(default_factory=list)  # uids


def _entries(path: str) -> list[dict[str, list[str]]]:
    entries: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith("#"):
                continue
            if not line.strip():
                if current:
                    entries.append(current)
                    current = {}
                continue
            key, _, value = line.partition(":")
            current.setdefault(key.strip().lower(), []).append(value.strip())
    if current:
        entries.append(current)
    return entries


def load(path: str) -> tuple[list[Person], list[UnixGroup]]:
    people: list[Person] = []
    groups: list[UnixGroup] = []
    for e in _entries(path):
        classes = {c.lower() for c in e.get("objectclass", [])}
        if "posixaccount" in classes:
            people.append(Person(uid=e["uid"][0],
                                 uid_number=int(e["uidnumber"][0]),
                                 gid_number=int(e["gidnumber"][0]),
                                 name=e["cn"][0]))
        elif "posixgroup" in classes:
            members = [dn.split(",", 1)[0].split("=", 1)[1]
                       for dn in e.get("member", [])]
            groups.append(UnixGroup(cn=e["cn"][0],
                                    gid_number=int(e["gidnumber"][0]),
                                    members=members))
    return people, groups
