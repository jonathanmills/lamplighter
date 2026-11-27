# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""A simulated Lustre jobstats exporter for the dev Prometheus.

Serves Prometheus counters that grow in real time, telling the same
story as gen_jobstats.py: dd.20005 writes, python3.20001 reads,
tar.20002 is light, find.20003 runs a getattr storm for 10 minutes of
every 30, rsync.20006's counters reset every 20 minutes, lfs.0 is idle.

Metric and label names are this simulation's own; real exporters differ,
which is why Lamplighter maps them in config.yaml (jobstats.prometheus):

  lustre_job_stats_total{server, target, jobid, operation}   metadata requests
  lustre_job_read_bytes_total{server, target, jobid}
  lustre_job_write_bytes_total{server, target, jobid}

Standard library only. Usage: fake_jobstats_exporter.py [PORT]
"""

import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

START = time.time()
MiB = 1 << 20
MDT = ("mds1", "lustre-MDT0000")
OSTS = [("oss1", "lustre-OST0000"), ("oss1", "lustre-OST0001"),
        ("oss2", "lustre-OST0002"), ("oss2", "lustre-OST0003")]

# per-second rates: metadata {op: req/s}, read and write bytes/s (whole job)
JOBS = {
    "dd.20005": ({"open": 0.03, "close": 0.03, "getattr": 0.1}, 0, 16 * MiB),
    "python3.20001": ({"open": 0.7, "close": 0.7, "getattr": 3}, 6 * MiB, 0),
    "tar.20002": ({"open": 0.25, "close": 0.25, "getattr": 0.3, "mknod": 0.25}, 0, 0.4 * MiB),
    "rsync.20006": ({"open": 5, "close": 5, "getattr": 15, "setattr": 5, "rename": 4.5}, 0.7 * MiB, 2 * MiB),
    "lfs.0": ({"statfs": 0.01}, 0, 0),
}
STORM = ("find.20003", {"getattr": 850, "statfs": 0.05})


def active_seconds(job: str, now: float) -> float:
    """Seconds this job's counters have been accumulating."""
    elapsed = now - START
    if job == "rsync.20006":
        return elapsed % 1200                       # entry recreated every 20 minutes
    return elapsed


def storm_seconds(now: float) -> float:
    """Seconds of storm so far: 10 minutes in every 30."""
    elapsed = now - START
    cycles, rest = divmod(elapsed, 1800)
    return cycles * 600 + min(rest, 600)


def metrics() -> str:
    now = time.time()
    out = ["# HELP lustre_job_stats_total Simulated metadata requests per job.",
           "# TYPE lustre_job_stats_total counter"]
    reads, writes = [], []
    for job, (md, rd, wr) in JOBS.items():
        t = active_seconds(job, now)
        for op, r in md.items():
            out.append(f'lustre_job_stats_total{{server="{MDT[0]}",target="{MDT[1]}",jobid="{job}",operation="{op}"}} {int(r * t)}')
        for server, target in OSTS:
            if rd:
                reads.append(f'lustre_job_read_bytes_total{{server="{server}",target="{target}",jobid="{job}"}} {int(rd * t / len(OSTS))}')
            if wr:
                writes.append(f'lustre_job_write_bytes_total{{server="{server}",target="{target}",jobid="{job}"}} {int(wr * t / len(OSTS))}')
    storm = storm_seconds(now)
    for op, r in STORM[1].items():
        out.append(f'lustre_job_stats_total{{server="{MDT[0]}",target="{MDT[1]}",jobid="{STORM[0]}",operation="{op}"}} {int(r * storm)}')
    out += ["# HELP lustre_job_read_bytes_total Simulated bytes read per job.",
            "# TYPE lustre_job_read_bytes_total counter"] + reads
    out += ["# HELP lustre_job_write_bytes_total Simulated bytes written per job.",
            "# TYPE lustre_job_write_bytes_total counter"] + writes
    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = metrics().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9169
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
