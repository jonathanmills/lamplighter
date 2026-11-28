#!/usr/bin/env python3.12
# SPDX-License-Identifier: CECILL-C
# Copyright (C) 2026 Jonathan Mills
"""Side-by-side tests (doc/RUST-PLAN.md): run each case's reference and
candidate commands on the same data and compare exit code, standard output
and standard error.

Cases are TOML files (cases/*.toml), one [[case]] table each:

    [[case]]
    name = "lux-du --version"
    reference = ["{ref}/src/robinhood/lux-du", "--version"]
    candidate = ["{rust}/examples/version_banner", "'du' command"]
    sort = false        # optional: compare sorted lines (no defined order)
    stderr = true       # optional: also compare standard error
    files = ["{src}/doc/templates/**/*.conf"]   # optional: one comparison per
                        # matching file, with {file} in the commands

Placeholders: {ref} the C reference build, {cur} the current C build,
{rust} cargo's release directory, {conf} the harness's engine config,
{cdump} the C cfgdump (c/cfgdump.c, linked with the reference parser),
{cload} the C cfgload (c/cfgload.c, the reference configuration modules),
{src} the repository, {here} this directory.
Log line prefixes (date, time, [pid/thread]) are normalized, since they
differ between any two runs. Each side gets LD_LIBRARY_PATH for the
engine modules of the build its command comes from.

Usage: difftest.py --ref DIR --cur DIR --rust DIR [--only NAME] cases/*.toml
"""

import argparse
import difflib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

LOG_PREFIX = re.compile(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d [^ \[\n]*\[\d+/\d+\] ", re.M)


def run(cmd: list[str], env_lib: str | None) -> tuple[int, str, str]:
    import os
    env = dict(os.environ)
    if env_lib:
        env["LD_LIBRARY_PATH"] = env_lib
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=env, timeout=300)
    return p.returncode, p.stdout, p.stderr


def normalize(text: str, sort: bool) -> list[str]:
    lines = LOG_PREFIX.sub("<log> ", text).splitlines()
    return sorted(lines) if sort else lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--cur", required=True)
    ap.add_argument("--rust", required=True)
    ap.add_argument("--conf", default=str(Path(__file__).parent / "difftest.conf"))
    ap.add_argument("--cdump", default="/build/difftest-c/cfgdump")
    ap.add_argument("--cload", default="/build/difftest-c/cfgload")
    ap.add_argument("--src", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--only")
    ap.add_argument("--quiet", action="store_true", help="print failures and the summary only")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    subst = {"ref": a.ref, "cur": a.cur, "rust": a.rust, "conf": a.conf, "cdump": a.cdump, "cload": a.cload,
             "src": a.src, "here": str(Path(__file__).resolve().parent)}

    def expand(cmd, file=None):
        return [part.format(**subst, file=file or "") for part in cmd]

    def libs(cmd):
        for build in (a.ref, a.cur):
            if cmd[0].startswith(build + "/"):
                return f"{build}/src/modules/.libs"
        return None

    import glob

    def instances(case):
        if "files" not in case:
            yield case["name"], None
            return
        found = sorted({f for pat in case["files"] for f in glob.glob(pat.format(**subst), recursive=True)})
        if not found:
            raise SystemExit(f"{case['name']}: no file matches {case['files']}")
        for f in found:
            yield f"{case['name']}: {f.replace(subst['src'] + '/', '')}", f

    failed, total = [], 0
    for f in a.files:
        for case in tomllib.loads(Path(f).read_text())["case"]:
          for name, file in instances(case):
            if a.only and a.only not in name:
                continue
            total += 1
            ref, cand = expand(case["reference"], file), expand(case["candidate"], file)
            r, c = run(ref, libs(ref)), run(cand, libs(cand))
            sort, with_err = case.get("sort", False), case.get("stderr", False)
            parts = [("exit code", [str(r[0])], [str(c[0])]),
                     ("stdout", normalize(r[1], sort), normalize(c[1], sort))]
            if with_err:
                parts.append(("stderr", normalize(r[2], sort), normalize(c[2], sort)))
            bad = [(what, x, y) for what, x, y in parts if x != y]
            if not bad:
                if not a.quiet:
                    print(f"  PASS  {name}  ({len(parts[1][1])} lines)")
                continue
            failed.append(name)
            print(f"  FAIL  {name}")
            for what, x, y in bad:
                print(f"        {what} differs:")
                for line in list(difflib.unified_diff(x, y, "reference", "candidate", lineterm="", n=1))[:40]:
                    print("        " + line)
    print(f"difftest: {total - len(failed)} of {total} cases identical")
    return 1 if failed or not total else 0


if __name__ == "__main__":
    sys.exit(main())
