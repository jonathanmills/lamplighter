# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Parallel scans of Robinhood's ENTRIES table, split by primary-key range.

At billions of entries a single connection scans one table at a time on
one core. The collector's plain scans (age histogram, fileclass and
status counts) only add up counts and sums, so they split cleanly: each
worker scans a range of ENTRIES' primary key (id), and the partial
results are added together, exactly.

Splitting a huge table into equal parts must not itself read the table.
InnoDB estimates the rows in a key range from a few index pages (what
EXPLAIN reports), so split points are found by bisecting the key space
on those estimates: a few dozen near-instant EXPLAINs, whatever the
table's size. The estimates are rough, so the table is cut into many
more chunks than workers and the workers take chunks from a queue:
an underestimated chunk only keeps its worker busy a little longer.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

Range = tuple[bytes | None, bytes | None]          # [lo, hi); None = unbounded

CHUNKS_PER_WORKER = 8


def _estimate(c: Connection, lo: bytes, hi: bytes) -> int:
    plan = c.execute(text("EXPLAIN FORMAT=JSON SELECT 1 FROM ENTRIES WHERE id >= :lo AND id < :hi"),
                     {"lo": lo, "hi": hi}).scalar()
    return int(json.loads(plan)["query_block"]["table"].get("rows", 0))


def split(c: Connection, parts: int) -> list[Range]:
    """Up to ``parts`` ranges of roughly equal (estimated) size, covering
    every id: the first starts unbounded, the last ends unbounded.

    Repeatedly halves the chunk that looks largest, at the midpoint of its
    key space, re-estimating only the two halves. Estimates are compared
    with their neighbours only, so a poor whole-table estimate does not
    matter, and any id format works (text ids sort as strings).
    """
    if parts <= 1:
        return [(None, None)]
    lo = c.execute(text("SELECT id FROM ENTRIES ORDER BY id LIMIT 1")).scalar()
    hi = c.execute(text("SELECT id FROM ENTRIES ORDER BY id DESC LIMIT 1")).scalar()
    if lo is None or lo == hi:
        return [(None, None)]
    width = max(len(lo), len(hi))

    def num(k: bytes) -> int:
        return int.from_bytes(k.ljust(width, b"\0"), "big")

    def key(n: int) -> bytes:
        return n.to_bytes(width, "big").rstrip(b"\0") or b"\0"

    a, b = num(lo), num(hi) + 1
    chunks = [(a, b, _estimate(c, key(a), key(b)))]          # [start, end) in key space
    # Text ids leave much of the byte space empty (a midpoint between "3"
    # and "4" sorts after every digit), so a half estimated empty (InnoDB says 1)
    # is not kept as a chunk: the range just narrows to the other half.
    for _ in range(parts * 64):
        if len(chunks) >= parts:
            break
        i = max(range(len(chunks)), key=lambda k: chunks[k][2])
        x, y, rows = chunks[i]
        if y - x < 2 or rows <= 1:
            break
        m = (x + y) // 2
        left, right = _estimate(c, key(x), key(m)), _estimate(c, key(m), key(y))
        if left <= 1:                      # InnoDB estimates an empty range as 1
            chunks[i] = (m, y, right)
        elif right <= 1:
            chunks[i] = (x, m, left)
        else:
            chunks[i:i + 1] = [(x, m, left), (m, y, right)]
    cuts = [key(x) for x, _, _ in chunks[1:]]
    bounds = [None, *cuts, None]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def where(r: Range, column: str = "id") -> tuple[str, dict]:
    """SQL condition and parameters for one range."""
    lo, hi = r
    conds, params = [], {}
    if lo is not None:
        conds.append(f"{column} >= :range_lo")
        params["range_lo"] = lo
    if hi is not None:
        conds.append(f"{column} < :range_hi")
        params["range_hi"] = hi
    return (" AND ".join(conds) or "TRUE"), params


def map_ranges(source: Engine, workers: int, fn: Callable[[Connection, Range], Any],
               setup: Callable[[Connection], None] | None = None,
               finish: Callable[[Connection], Any] | None = None) -> list[Any]:
    """Run fn(connection, range) over the whole table with ``workers``
    connections in parallel (the engine's pool must allow that many).

    Without ``finish``, returns fn's results, one per range. With it, each
    connection calls finish() after its last range and the results are one
    per connection: grouped scans fold their chunks into a temporary table
    on the server and return it once, so Python parses one result per
    worker instead of one per chunk (parsing rows holds Python's GIL, and
    every chunk of a GROUP BY uid scan returns nearly every user).
    """
    ranges = [(None, None)]
    if workers > 1:
        with source.connect() as c:
            ranges = split(c, workers * CHUNKS_PER_WORKER)
    workers = min(workers, len(ranges))
    todo: queue.Queue[Range] = queue.Queue()
    for r in ranges:
        todo.put(r)
    results: list[Any] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def work() -> None:
        try:
            with source.connect() as c:
                if setup:
                    setup(c)
                while not errors:
                    try:
                        r = todo.get_nowait()
                    except queue.Empty:
                        break
                    out = fn(c, r)
                    if finish is None:
                        with lock:
                            results.append(out)
                if finish is not None and not errors:
                    out = finish(c)
                    with lock:
                        results.append(out)
        except BaseException as exc:               # surfaced below, after all threads stop
            errors.append(exc)

    if workers <= 1:
        work()
    else:
        threads = [threading.Thread(target=work, name=f"scan-{i}") for i in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    if errors:
        raise errors[0]
    return results
