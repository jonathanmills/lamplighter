-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 Jonathan Mills
--
-- lamplighter_webui schema, version 5: entries and bytes per Robinhood fileclass
-- and policy status, per collector run (policies.py).
--   fileclass = <name>, status_field = ''        totals per fileclass ('' = unclassified)
--   fileclass = '*',    status_field = <column>  totals per status value
--   fileclass = <name>, status_field = <column>  per fileclass and status value
-- status_value '' means the policy never set a status on those entries.

CREATE TABLE policy_counts (
    run_id        BIGINT UNSIGNED NOT NULL,
    fileclass     VARCHAR(255) NOT NULL,
    status_field  VARCHAR(64) NOT NULL,
    status_value  VARCHAR(128) NOT NULL,
    entries       BIGINT UNSIGNED NOT NULL,
    bytes         BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, fileclass, status_field, status_value),
    CONSTRAINT policy_counts_run FOREIGN KEY (run_id) REFERENCES collector_runs (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
