# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Write sample Lustre jobstats snapshots into a spool directory.

Simulates what each Lustre server's cron one-liner would drop
(``lctl get_param mdt.*.job_stats obdfilter.*.job_stats``), one file per
server per minute over the last two hours:

- mds1 (lustre-MDT0000), oss1 (OST0000, OST0001), oss2 (OST0002, OST0003)
- oss2 stops reporting 20 minutes ago (shown as stale)
- dd.20005 writes steadily; python3.20001 reads; tar.20002 is light
- find.20003 starts a getattr storm 30 minutes ago
- rsync.20006's entries are dropped for 10 minutes, then start again
  from zero (a counter reset)
- lfs.0 (root) does a little now and then

Usage: gen_jobstats.py SPOOL_DIR
"""

import random
import sys
import time
from pathlib import Path

SERVERS = {"mds1": ["lustre-MDT0000"], "oss1": ["lustre-OST0000", "lustre-OST0001"],
           "oss2": ["lustre-OST0002", "lustre-OST0003"]}
MD_OPS = ["open", "close", "mknod", "link", "unlink", "mkdir", "rmdir", "rename",
          "getattr", "setattr", "getxattr", "setxattr", "statfs", "sync"]
OST_OPS = ["getattr", "setattr", "punch", "sync", "destroy", "create", "statfs"]
MiB = 1 << 20


def activity(job: str, minute: int, total: int) -> tuple[dict, dict]:
    """Per-minute metadata ops and per-OST traffic for one job."""
    r = random.random
    md: dict[str, int] = {}
    io = {"read": 0, "write": 0, "punch": 0, "setattr": 0}
    late = minute >= total - 30
    if job == "dd.20005":
        md = {"open": 2, "close": 2, "getattr": 6}
        io["write"] = int((900 + 200 * r()) * MiB)
    elif job == "python3.20001":
        md = {"open": 40, "close": 40, "getattr": 120 + int(60 * r())}
        io["read"] = int((300 + 150 * r()) * MiB)
    elif job == "tar.20002":
        md = {"open": 15, "close": 15, "getattr": 20, "mknod": 15}
        io["write"] = int((20 + 10 * r()) * MiB)
    elif job == "find.20003":
        if late:
            md = {"getattr": 45000 + int(10000 * r()), "statfs": 3}
    elif job == "rsync.20006":
        md = {"open": 300, "close": 300, "getattr": 900, "mkdir": 20, "setattr": 300, "unlink": 50,
              "rename": 280}
        io["write"] = int((120 + 60 * r()) * MiB)
        io["read"] = int((40 + 20 * r()) * MiB)
        io["setattr"] = 10
    elif job == "lfs.0":
        if minute % 15 == 0:
            md = {"statfs": 4, "getattr": 10}
    return md, io


def fmt_reqs(n: int) -> str:
    return f"{{ samples: {n:>11}, unit:  reqs }}"


def fmt_bytes(samples: int, total: int, size: int) -> str:
    mn = size if samples else 0
    return (f"{{ samples: {samples:>11}, unit: bytes, min: {mn:>8}, max: {mn:>8}, "
            f"sum: {total:>16}, sumsq: {total * size:>20} }}")


def main(spool: str) -> None:
    random.seed(42)
    out = Path(spool)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.iterdir():
        if f.name.endswith((".jobstats", ".done", ".rejected", ".tmp")):
            f.unlink()

    jobs = ["dd.20005", "python3.20001", "tar.20002", "find.20003", "rsync.20006", "lfs.0"]
    total = 120
    now = int(time.time()) // 60 * 60
    start = now - total * 60
    # cumulative counters: (server target, job) -> {op: [samples, sum]}
    counters: dict[tuple[str, str], dict[str, list[int]]] = {}

    for minute in range(total + 1):
        t = start + minute * 60
        for host, targets in SERVERS.items():
            if host == "oss2" and minute > total - 20:
                continue                                    # oss2 went quiet
            lines = []
            for target in targets:
                lines.append(f"{'mdt' if 'MDT' in target else 'obdfilter'}.{target}.job_stats=")
                lines.append("job_stats:")
                for job in jobs:
                    if job == "rsync.20006" and 60 <= minute < 70:
                        counters.pop((target, job), None)   # entry dropped (idle cleanup)
                        continue
                    md, io = activity(job, minute, total)
                    c = counters.setdefault((target, job), {})
                    if "MDT" in target:
                        for op, n in md.items():
                            c.setdefault(op, [0, 0])[0] += n
                    else:
                        share = 1 / len([x for s in SERVERS.values() for x in s if "OST" in x])
                        for op, size in (("read", 4 * MiB), ("write", 4 * MiB)):
                            b = int(io[op] * share)
                            if b:
                                k = c.setdefault(f"{op}_bytes", [0, 0])
                                k[0] += max(1, b // size)
                                k[1] += b
                        for op in ("punch", "setattr"):
                            if io[op]:
                                c.setdefault(op, [0, 0])[0] += io[op]
                    if not c:
                        continue
                    lines.append(f"- job_id:          {job}")
                    lines.append(f"  snapshot_time:   {t}.000000000 secs.nsecs")
                    for op in (MD_OPS if "MDT" in target else ["read_bytes", "write_bytes"] + OST_OPS):
                        s, b = c.get(op, [0, 0])
                        if op.endswith("_bytes"):
                            lines.append(f"  {op + ':':<16} {fmt_bytes(s, b, 4 * MiB)}")
                        else:
                            lines.append(f"  {op + ':':<16} {fmt_reqs(s)}")
            name = out / f"{host}.{t}.jobstats"
            name.with_suffix(".tmp").write_text("\n".join(lines) + "\n")
            name.with_suffix(".tmp").rename(name)
    print(f"lamplighter-dev: wrote {len(list(out.glob('*.jobstats')))} jobstats snapshots to {out}")


if __name__ == "__main__":
    main(sys.argv[1])
