-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 Jonathan Mills
--
-- lamplighter_webui schema, version 7: each owner's share of every rolled-up
-- directory, for own-files browsing (paths.view_own): a person sees the
-- directories that hold their data, with their own totals only.
-- Kept for the latest collector run only (rows: tracked directories x the
-- owners with data below each), unlike dir_rollup, which keeps keep_runs.

CREATE TABLE dir_rollup_owner (
    run_id           BIGINT UNSIGNED NOT NULL,
    owner            VARCHAR(127) NOT NULL,
    dir_id           VARBINARY(64) NOT NULL,
    parent_dir       VARBINARY(64) NULL,          -- NULL for the filesystem root
    files            BIGINT UNSIGNED NOT NULL,
    bytes            BIGINT UNSIGNED NOT NULL,
    newest_access    INT UNSIGNED NULL,
    oldest_access    INT UNSIGNED NULL,
    cold_1y_bytes    BIGINT UNSIGNED NOT NULL,
    cold_3y_bytes    BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, owner, dir_id),
    KEY by_parent (run_id, owner, parent_dir),
    CONSTRAINT dir_rollup_owner_run FOREIGN KEY (run_id) REFERENCES collector_runs (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
