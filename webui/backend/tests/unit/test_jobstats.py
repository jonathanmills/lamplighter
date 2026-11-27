# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Jobstats parsing, intervals and the spool directory (no database)."""

import os

import pytest

from lux_webui.jobstats import SpoolSource, intervals_between, parse_snapshot

MDT_212 = """\
mdt.lustre-MDT0000.job_stats=
job_stats:
- job_id:          dd.20001
  snapshot_time:   1795000000
  open:            { samples:          10, unit:  reqs }
  close:           { samples:          10, unit:  reqs }
  mknod:           { samples:           0, unit:  reqs }
  getattr:         { samples:         120, unit:  reqs }
- job_id:          find.20003
  snapshot_time:   1795000000
  getattr:         { samples:        5000, unit:  reqs }
  statfs:          { samples:           2, unit:  reqs }
"""

# Lustre 2.15: start_time/elapsed_time, sumsq, latency ("usecs") lines
OST_215 = """\
obdfilter.lustre-OST0001.job_stats=
job_stats:
- job_id:          dd.20001
  snapshot_time:   1795000000.123456789 secs.nsecs
  start_time:      1794999000.000000000 secs.nsecs
  elapsed_time:    1000.123456789 secs.nsecs
  read_bytes:      { samples:           0, unit: bytes, min:        0, max:        0, sum:                0, sumsq:                  0 }
  write_bytes:     { samples:          10, unit: bytes, min:  1048576, max:  1048576, sum:         10485760, sumsq:   10995116277760 }
  read:            { samples:           0, unit: usecs, min:        0, max:        0, sum:                0, sumsq:                  0 }
  write:           { samples:          10, unit: usecs, min:      100, max:      900, sum:             4000, sumsq:            2000000 }
  punch:           { samples:           1, unit:  reqs }
"""


def test_parse_both_formats():
    snap = parse_snapshot(MDT_212 + OST_215)
    assert set(snap) == {"lustre-MDT0000", "lustre-OST0001"}
    assert snap["lustre-MDT0000"]["find.20003"]["getattr"] == (5000, 0)
    ost = snap["lustre-OST0001"]["dd.20001"]
    assert ost["write_bytes"] == (10, 10485760)
    assert "write" not in ost            # latency (usecs) is not traffic
    assert ost["punch"] == (1, 0)


def test_garbage_and_hostile_input_is_harmless():
    assert parse_snapshot("") == {}
    assert parse_snapshot("job_stats:\n- job_id: x\n") == {}          # no target header
    snap = parse_snapshot("mdt.fs-MDT0000.job_stats=\n- job_id: \"a\x1b[31mb\"\n"
                          "  open: { samples: notanumber, unit: reqs }\n"
                          "  close: { samples: 3, unit: reqs }\n- job_id: " + "x" * 1000 + "\n")
    jobs = snap["fs-MDT0000"]
    assert "a?[31mb" in jobs                                            # control chars replaced
    assert jobs["a?[31mb"] == {"close": (3, 0)}
    assert max(len(j) for j in jobs) == 255


def snap(target, job, **ops):
    return {target: {job: {op: v for op, v in ops.items()}}}


def test_intervals_difference_and_classification():
    prev = parse_snapshot(MDT_212 + OST_215)
    cur = parse_snapshot(MDT_212.replace("120, unit", "150, unit").replace("5000", "9000")
                         + OST_215.replace("10485760", "31457280").replace("samples:          10, unit: bytes",
                                                                           "samples:          30, unit: bytes"))
    ivs = {(i.target, i.job_id): i for i in intervals_between("oss1", 100, prev, 160, cur)}
    md = ivs[("lustre-MDT0000", "dd.20001")]
    assert (md.md_ops, md.op_counts) == (30, {"getattr": 30})
    assert ivs[("lustre-MDT0000", "find.20003")].md_ops == 4000
    ost = ivs[("lustre-OST0001", "dd.20001")]
    assert (ost.write_bytes, ost.write_ops, ost.read_bytes, ost.other_ops) == (20971520, 20, 0, 0)
    assert (ost.t_start, ost.t_end, ost.source) == (100, 160, "oss1")


def test_new_job_reset_and_idle_jobs():
    prev = snap("fs-MDT0000", "a.1", open=(100, 0), close=(100, 0))
    prev["fs-MDT0000"]["idle.2"] = {"open": (5, 0)}
    cur = snap("fs-MDT0000", "a.1", open=(7, 0), close=(150, 0))     # open went down: reset
    cur["fs-MDT0000"]["idle.2"] = {"open": (5, 0)}                    # no activity
    cur["fs-MDT0000"]["new.3"] = {"mkdir": (4, 0)}                    # appeared in between
    ivs = {i.job_id: i for i in intervals_between("mds1", 0, prev, 60, cur)}
    assert ivs["a.1"].op_counts == {"open": 7, "close": 150}         # whole entry counted from zero
    assert ivs["new.3"].md_ops == 4
    assert "idle.2" not in ivs


# --- spool ---------------------------------------------------------------------

def write(d, name, content=MDT_212):
    (d / name).write_text(content)
    return d / name


def test_spool_processes_in_order_and_keeps_one_baseline(tmp_path):
    src = SpoolSource(str(tmp_path), max_file_bytes=1 << 20, max_interval_seconds=900)
    write(tmp_path, "mds1.1795000000.jobstats")
    ivs, st = src.collect()
    assert ivs == [] and st[0].last_snapshot == 1795000000        # first file: baseline only
    write(tmp_path, "mds1.1795000060.jobstats", MDT_212.replace("5000", "5600"))
    write(tmp_path, "mds1.1795000120.jobstats", MDT_212.replace("5000", "6600"))
    ivs, st = src.collect()
    got = sorted((i.t_start, i.t_end, i.md_ops) for i in ivs if i.job_id == "find.20003")
    assert got == [(1795000000, 1795000060, 600), (1795000060, 1795000120, 1000)]
    assert sorted(os.listdir(tmp_path)) == ["mds1.1795000120.jobstats.done"]
    assert st[0].targets == ["lustre-MDT0000"] and st[0].jobs == 2


def test_spool_gap_starts_a_new_baseline(tmp_path):
    src = SpoolSource(str(tmp_path), max_file_bytes=1 << 20, max_interval_seconds=900)
    write(tmp_path, "mds1.1795000000.jobstats")
    src.collect()
    write(tmp_path, "mds1.1795009000.jobstats", MDT_212.replace("5000", "9999"))
    ivs, _ = src.collect()
    assert ivs == []                                                  # 2.5 h gap: not smeared


def test_spool_rejects_untrusted_files(tmp_path):
    src = SpoolSource(str(tmp_path), max_file_bytes=200, max_interval_seconds=900)
    write(tmp_path, "oss1.1795000000.jobstats", "x" * 500)            # too large
    os.symlink("/etc/passwd", tmp_path / "oss2.1795000000.jobstats")  # symlink
    write(tmp_path, "notes.txt", "ignored")                           # not a snapshot name
    write(tmp_path, "mds1.1795000000.jobstats.tmp", "in flight")      # not finished
    ivs, st = src.collect()
    errors = {s.source: s.error for s in st}
    assert "larger than" in errors["oss1"]
    assert errors["oss2"]                                             # O_NOFOLLOW refused it
    assert sorted(os.listdir(tmp_path)) == [
        "mds1.1795000000.jobstats.tmp", "notes.txt",
        "oss1.1795000000.jobstats.rejected", "oss2.1795000000.jobstats.rejected"]


def test_spool_missing_directory_is_an_error(tmp_path):
    with pytest.raises(RuntimeError):
        SpoolSource(str(tmp_path / "nope"), 1 << 20, 900).collect()
