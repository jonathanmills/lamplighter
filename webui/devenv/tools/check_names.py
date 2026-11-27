#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Enforce Lamplighter's naming rule (CLAUDE.md, doc/COMPATIBILITY.md).

Only tools for any user, installed in /usr/bin, use the "lux-" prefix:
lux-find and lux-du. Everything else is named "lamplighter": the daemon,
the /usr/sbin tools, helpers, services, users, paths, packages, schemas
and environment variables. This check fails on any other "lux" name in
a tracked file. Prose about the prefix itself ("lux-", "Lux") is fine.

Usage: check_names.py [REPO]   (default: the repository holding this file)
"""

import re
import subprocess
import sys
from pathlib import Path

ALLOWED = {"find", "du"}                       # lux-find, lux-du (and lux_find_* build variables)
EXEMPT = {"doc/NAMING.md", "doc/ROBINHOOD-README.md", "ChangeLog",
          "webui/frontend/package-lock.json", "webui/devenv/tools/check_names.py"}
BINARY = re.compile(r"\.(png|ico|webp|jpg|gif|mkp|rpm|psd|pdf|gz|woff2?)$")
NAME = re.compile(r"\b[Ll][Uu][Xx](?:[-_]([A-Za-z][A-Za-z0-9_.]*)|([A-Za-z]+))")


def violations(repo: Path):
    files = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True,
                           check=True).stdout.split("\n")
    for name in files:
        if not name or name in EXEMPT or BINARY.search(name):
            continue
        if re.search(r"(^|/)lux", name) and not re.search(r"(^|/)lux-(find|du)\.1$", name):
            yield name, 0, Path(name).name
        try:
            text = (repo / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in NAME.finditer(line):
                suffix, word = m.group(1), m.group(2)
                if suffix is not None and re.split(r"[_.]", suffix)[0].lower() in ALLOWED:
                    continue
                if word is not None:
                    yield name, n, m.group(0)     # e.g. "luxext": always a name
                    continue
                yield name, n, m.group(0)


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]
    found = list(violations(repo))
    for name, n, token in found:
        print(f"{name}:{n}: '{token}': only lux-find and lux-du may use the lux prefix; "
              "everything else is named lamplighter")
    if found:
        return 1
    print("naming: ok (lux- only for lux-find and lux-du)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
