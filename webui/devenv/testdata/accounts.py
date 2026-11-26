# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Create local Unix groups and users matching the LDAP test data.

Run as root in the workbench (the entrypoint does this). Idempotent.
Robinhood resolves uid/gid numbers to names through NSS, so these
local accounts stand in for sssd/LDAP on a real Robinhood server.
"""

import grp
import pwd
import subprocess
import sys

from ldifdata import load


def main(ldif: str) -> None:
    people, groups = load(ldif)
    for g in groups:
        try:
            grp.getgrgid(g.gid_number)
        except KeyError:
            subprocess.run(["groupadd", "-g", str(g.gid_number), g.cn], check=True)
    for p in people:
        try:
            pwd.getpwuid(p.uid_number)
        except KeyError:
            subprocess.run(["useradd", "-M", "-N", "-u", str(p.uid_number),
                            "-g", str(p.gid_number), "-c", p.name,
                            "-s", "/sbin/nologin", p.uid], check=True)
    for g in groups:
        for uid in g.members:
            subprocess.run(["usermod", "-a", "-G", g.cn, uid], check=True)


if __name__ == "__main__":
    main(sys.argv[1])
