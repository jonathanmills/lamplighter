# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Demo scheduled reports for screenshots: two reports owned by carol,
each run eight times, with the runs spread over the past eight weeks so
the custom graph has a time axis. Replaces existing reports of the same
names. Run inside the webui container (devenv.sh shots does)."""

import time

from sqlalchemy import text

from lamplighter_webui import reports as R
from lamplighter_webui.config import load_settings

DEMOS = [
    {"name": "Weekly cold owners", "source": "cold-owners", "params": {"older_than": "1y"}, "schedule": "weekly",
     "keep_runs": 12, "recipients": ["carol"],
     "chart": {"value": "bytes_not_accessed_1y", "stack_by": "user", "top": 4}},
    {"name": "Group usage", "source": "groups", "params": {}, "schedule": "daily", "keep_runs": 30,
     "recipients": [], "chart": {"value": "bytes", "stack_by": "group", "top": 5}},
]

settings = load_settings()
state = R.make_state(settings)
with state.store_engine.begin() as c:
    c.execute(text("DELETE FROM reports WHERE name IN :n").bindparams(
        __import__("sqlalchemy").bindparam("n", expanding=True)), {"n": [d["name"] for d in DEMOS]})
now = int(time.time())
for demo in DEMOS:
    rid = R.save_report(state.store_engine, R.validate(settings, demo), "carol")
    report = R.get_report(state.store_engine, rid)
    for week in range(8):
        R.run(state, report, "schedule")
    with state.store_engine.begin() as c:
        ids = [i for (i,) in c.execute(text("SELECT id FROM report_runs WHERE report_id = :r ORDER BY id"), {"r": rid})]
        for n, run_id in enumerate(ids):
            # spread over eight weeks (the dev data itself does not change)
            c.execute(text("UPDATE report_runs SET started_at = :t WHERE id = :i"),
                      {"t": now - (len(ids) - 1 - n) * 7 * 86400, "i": run_id})
    print(f"seeded {demo['name']!r} (id {rid}) with {len(ids)} runs")
