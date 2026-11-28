#!/usr/bin/env python3
# SPDX-License-Identifier: CECILL-C
# Copyright (C) 2026 Jonathan Mills
"""Check the licences of every crate in Lamplighter's Rust workspace.

Lamplighter's engine is CeCILL-C, and the project accepts only
dependencies compatible with GPLv3 (doc/RUST-PLAN.md). This reads
`cargo metadata` and evaluates each package's SPDX licence expression:
"A OR B" passes if either side is allowed, "A AND B" only if both are,
"A WITH exception" as A. A package with no licence field fails.

Usage: check_licences.py [path/to/Cargo.toml]   (run by `make check`)
"""

import json
import re
import subprocess
import sys
from pathlib import Path

# Permissive licences compatible with CeCILL-C and GPLv3, and our own.
ALLOWED = {
    "CECILL-C",
    "MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Zlib",
    "0BSD", "CC0-1.0", "Unlicense", "Unicode-DFS-2016", "Unicode-3.0", "BSL-1.0",
    "MPL-2.0",
}


def allowed(expr: str) -> bool:
    """Evaluate an SPDX expression against ALLOWED (OR, AND, WITH, parentheses)."""
    tokens = re.findall(r"\(|\)|[A-Za-z0-9.+-]+", expr.replace("/", " OR "))  # old "MIT/Apache-2.0"
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def term() -> bool:
        if peek() == "(":
            take()
            v = disjunction()
            if take() != ")":
                raise ValueError(expr)
            return v
        lic = take()
        if peek() == "WITH":
            take()
            take()                            # the exception only adds permissions
        return lic in ALLOWED

    def conjunction() -> bool:
        v = term()
        while peek() == "AND":
            take()
            v = term() and v
        return v

    def disjunction() -> bool:
        v = conjunction()
        while peek() == "OR":
            take()
            v = conjunction() or v
        return v

    result = disjunction()
    if pos != len(tokens):
        raise ValueError(expr)
    return result


def main() -> int:
    manifest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "Cargo.toml"
    meta = json.loads(subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--locked", "--manifest-path", str(manifest)],
        capture_output=True, text=True, check=True).stdout)
    bad = []
    for p in sorted(meta["packages"], key=lambda p: p["name"]):
        lic = p.get("license")
        try:
            ok = bool(lic) and allowed(lic)
        except (ValueError, IndexError):
            ok = False
        print(f"{'ok ' if ok else 'BAD'} {p['name']} {p['version']}: {lic or '(no licence field)'}")
        if not ok:
            bad.append(p["name"])
    if bad:
        print(f"licences: {len(bad)} crate(s) not allowed: {', '.join(bad)}", file=sys.stderr)
        return 1
    print(f"licences: ok ({len(meta['packages'])} crates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
