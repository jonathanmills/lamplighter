# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Generate a realistic test file tree for Robinhood to scan.

Files are sparse (their size costs no space), owned by the LDAP test
users, and given access/modification times that tell a story the web
UI views should reveal:

  frank  genomics  cold: large datasets untouched for 2-6 years
  erin   climate   big model output, 1 month to 1 year old
  alice  astro     active: mostly hot data, touched in the last weeks
  bob    astro     a burst of many tiny files in scratch in the last
                   2 hours (the classic "the cluster is slow" pattern)
  carol, dave      staff: small home directories

Deterministic (fixed seed). Usage: generate.py ROOT LDIF [--scale N]
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import time

from ldifdata import load

DAY = 86400
KB, MB, GB = 1024, 1024**2, 1024**3


def make_file(path: str, size: int, age_mtime: float, age_atime: float,
              uid: int, gid: int, now: float) -> None:
    with open(path, "wb") as fh:
        fh.truncate(size)
    mtime = now - age_mtime
    atime = max(mtime, now - age_atime)  # atime is never older than mtime
    os.utime(path, (atime, mtime))
    os.chown(path, uid, gid)


def make_dir(path: str, uid: int, gid: int) -> None:
    os.makedirs(path, exist_ok=True)
    os.chown(path, uid, gid)


def fix_dir_times(root: str) -> None:
    """Set each directory's times to its newest child's (bottom-up)."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        newest = 0.0
        for name in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, name))
            newest = max(newest, st.st_mtime)
        if newest:
            os.utime(dirpath, (newest, newest))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("ldif")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="multiply file counts (default 1.0, ~25k files)")
    args = ap.parse_args()

    rnd = random.Random(20261126)
    now = time.time()
    people, groups = load(args.ldif)
    user = {p.uid: p for p in people}
    gname = {g.gid_number: g.cn for g in groups}

    def n(count: int) -> int:
        return max(1, int(count * args.scale))

    # Start clean (root itself is a mount point: empty it, keep it).
    for entry in os.listdir(args.root):
        p = os.path.join(args.root, entry)
        shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)

    for top in ("home", "projects", "scratch"):
        make_dir(os.path.join(args.root, top), 0, 0)
    for g in groups:
        if g.cn.startswith("proj-"):
            make_dir(os.path.join(args.root, "projects", g.cn), 0, g.gid_number)

    count = 0

    def dataset(uid: str, base: str, ndirs: int, files_per_dir: int,
                size_fn, mage_fn, aage_fn) -> None:
        nonlocal count
        p = user[uid]
        make_dir(base, p.uid_number, p.gid_number)
        for d in range(ndirs):
            dpath = os.path.join(base, f"set{d:03d}")
            make_dir(dpath, p.uid_number, p.gid_number)
            for f in range(files_per_dir):
                mage = mage_fn()
                make_file(os.path.join(dpath, f"part{f:05d}.dat"), size_fn(),
                          mage, min(mage, aage_fn()), p.uid_number,
                          p.gid_number, now)
                count += 1

    lognorm = lambda mu, sigma, cap: lambda: int(min(cap, rnd.lognormvariate(mu, sigma)))
    uniform_days = lambda lo, hi: lambda: rnd.uniform(lo * DAY, hi * DAY)

    # frank: cold genomics archive, 2-6 years untouched
    dataset("frank", os.path.join(args.root, "projects/proj-genomics/frank/sequencing-2021"),
            n(40), n(150), lognorm(19.5, 1.5, 200 * GB),
            uniform_days(730, 2190), uniform_days(730, 2190))
    # erin: climate model output, 1 month - 1 year, rarely re-read
    dataset("erin", os.path.join(args.root, "projects/proj-climate/erin/cmip-runs"),
            n(30), n(120), lognorm(21.0, 1.2, 1024 * GB),
            uniform_days(30, 365), uniform_days(20, 365))
    # alice: active astro analysis, mostly hot
    dataset("alice", os.path.join(args.root, "projects/proj-astro/alice/survey"),
            n(25), n(200), lognorm(16.0, 2.0, 50 * GB),
            uniform_days(0.01, 30), uniform_days(0.0, 7))
    # bob: small-file storm in scratch during the last 2 hours
    dataset("bob", os.path.join(args.root, "scratch/bob/job-4815162342"),
            n(60), n(150), lognorm(8.0, 1.0, 64 * KB),
            uniform_days(0.0, 2 / 24), uniform_days(0.0, 2 / 24))
    # staff homes: small, mixed ages
    for uid in ("carol", "dave", "alice", "bob", "erin", "frank"):
        dataset(uid, os.path.join(args.root, "home", uid), n(3), n(40),
                lognorm(12.0, 2.0, 2 * GB), uniform_days(1, 900),
                uniform_days(0, 900))

    fix_dir_times(args.root)
    print(f"lux-dev: generated {count} files under {args.root} "
          f"for {len(people)} users in {len(gname)} Unix groups")


if __name__ == "__main__":
    main()
