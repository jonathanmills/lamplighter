#!/usr/bin/env python3.12
# SPDX-License-Identifier: CECILL-C
# Copyright (C) 2026 Jonathan Mills
"""Differential fuzzing of the configuration parser (stage 1,
doc/RUST-PLAN.md): mutate real configurations at random and require the
C parser (c/cfgdump.c) and the Rust one (examples/cfgdump) to give the
same output, warnings and exit code on every mutant.

Mutations: byte edits (delete, duplicate, swap, random bytes including
NUL and non-ASCII), grammar tokens inserted (braces, operators,
keywords, quotes, comments, %include, $VARIABLES), splices of two files,
and deep nesting near the parser's 10,000-entry stack limit.

A mismatch is saved in --save (default /build/fuzz-failures) with both
outputs, and the run fails. Deterministic for a given --seed.

Usage: fuzz_config.py --cdump C --rdump RUST [--iterations N] [--seed S] CORPUS...
"""

import argparse
import glob
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

TOKENS = [b"{", b"}", b"(", b")", b";", b",", b"=", b"==", b"!=", b"<>", b">", b">=", b"<", b"<=",
          b" and ", b" or ", b" not ", b" union ", b" inter ", b" AND ", b"Not", b"\"", b"'", b"\\",
          b"#", b"//", b"%include ", b"%include \"", b"\n", b"\\\n", b"$", b"$LL_TEST_VAR",
          b"$LL_TEST_UNDEFINED", b"x", b"10GB", b"/a/b", b"name", b" ", b"\t", b"\r", b"\f",
          b"\x00", b"\xff", b"\xe9", b"block id { ", b"a == 1", b"k = v;", b"f(x)", b"(a, b)"]


def mutate(rng: random.Random, data: bytes, corpus: list[bytes]) -> bytes:
    b = bytearray(data)
    for _ in range(rng.choice([1, 1, 1, 1, 2, 3, 6])):   # mostly light: more mutants still parse
        op = rng.random()
        pos = rng.randint(0, len(b)) if b else 0
        if op < 0.25 and b:                                   # delete a range
            del b[pos:pos + rng.randint(1, 8)]
        elif op < 0.45:                                        # insert a token
            b[pos:pos] = rng.choice(TOKENS)
        elif op < 0.55 and b:                                  # duplicate a range
            end = min(len(b), pos + rng.randint(1, 40))
            b[pos:pos] = b[pos:end]
        elif op < 0.65 and len(b) > 2:                         # swap two bytes
            i, j = rng.randrange(len(b)), rng.randrange(len(b))
            b[i], b[j] = b[j], b[i]
        elif op < 0.75:                                        # random bytes
            b[pos:pos] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 4)))
        elif op < 0.85 and corpus:                             # splice another file
            other = rng.choice(corpus)
            s = rng.randint(0, len(other))
            b[pos:pos] = other[s:s + rng.randint(1, 200)]
        else:                                                  # truncate
            del b[pos:]
    return bytes(b)


def deep(rng: random.Random) -> bytes:
    n = rng.choice([9990, 9995, 9997, 9998, 9999, 10000, 10001, 10005, 5000, 20000])
    kind = rng.choice(["paren", "set", "not", "block"])
    if kind == "paren":
        return b"a {\n b { " + b"(" * n + b"x == 1" + b")" * n + b" }\n}\n"
    if kind == "set":
        return b"a {\n b { " + b"(" * n + b"x" + b")" * n + b" }\n}\n"
    if kind == "not":
        return b"a {\n b { " + b"not (" * n + b"x == 1" + b")" * n + b" }\n}\n"
    return b"a " + b"b { " * n + b"x = 1;" + b"}" * n + b"\n"


def run(cmd: list[str]) -> tuple[int, bytes, bytes]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=20)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -999, b"TIMEOUT", b""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdump", required=True)
    ap.add_argument("--rdump", required=True)
    ap.add_argument("--iterations", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--save", default="/build/fuzz-failures")
    ap.add_argument("--max-failures", type=int, default=10)
    ap.add_argument("corpus", nargs="+")
    a = ap.parse_args()
    files = sorted({f for pat in a.corpus for f in glob.glob(pat, recursive=True) if os.path.isfile(f)})
    corpus = [Path(f).read_bytes() for f in files]
    rng = random.Random(a.seed)
    failures = 0
    outcomes = {"ok": 0, "error": 0, "other": 0}
    others: dict = {}
    # mutants live next to the corpus' include files, so relative %include
    # statements resolve to real files sometimes
    with tempfile.TemporaryDirectory(prefix="fuzz-") as tmp:
        path = os.path.join(tmp, "mutant.conf")
        for i in range(a.iterations):
            data = deep(rng) if i % 200 == 199 else mutate(rng, rng.choice(corpus), corpus)
            Path(path).write_bytes(data)
            c, r = run([a.cdump, path]), run([a.rdump, path])
            kind = "ok" if c[0] == 0 else "error" if c[0] == 1 else "other"
            outcomes[kind] += 1
            if kind == "other":
                others.setdefault((c[0], c[2][:60]), 0)
                others[(c[0], c[2][:60])] += 1
            if c != r:
                failures += 1
                os.makedirs(a.save, exist_ok=True)
                base = os.path.join(a.save, f"seed{a.seed}-{i}")
                Path(base + ".conf").write_bytes(data)
                Path(base + ".c.out").write_bytes(b"rc=%d\n" % c[0] + c[1] + b"\n--stderr--\n" + c[2])
                Path(base + ".rust.out").write_bytes(b"rc=%d\n" % r[0] + r[1] + b"\n--stderr--\n" + r[2])
                print(f"  FAIL  mutant {i}: saved {base}.conf (C rc={c[0]}, Rust rc={r[0]})")
                if failures >= a.max_failures:
                    break
    print(f"fuzz: {a.iterations if failures < a.max_failures else i + 1} mutants, {failures} different "
          f"(C outcomes: {outcomes['ok']} parsed, {outcomes['error']} errors, {outcomes['other']} other)")
    for (rc, err), n in sorted(others.items(), key=lambda kv: -kv[1]):
        print(f"      other outcome x{n}: exit {rc}, stderr {err!r}")
    if any(rc == -999 for rc, _ in others):
        print("fuzz: timeouts on both sides are not a comparison: investigate")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
