-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 Jonathan Mills
--
-- lamplighter_webui schema, version 3: per-directory subtree totals down to
-- collector.rollup_depth, for Browse and the coldest-directories view.

CREATE TABLE dir_rollup (
    run_id           BIGINT UNSIGNED NOT NULL,
    dir_id           VARBINARY(64) NOT NULL,      -- Robinhood entry id
    parent_dir       VARBINARY(64) NULL,          -- NULL for the filesystem root
    depth            TINYINT UNSIGNED NOT NULL,   -- 0 = root
    path             VARCHAR(4096) NOT NULL,
    owner            VARCHAR(127) NULL,           -- owner of the directory itself
    top_owner        VARCHAR(127) NULL,           -- owner of most bytes in the subtree
    top_owner_bytes  BIGINT UNSIGNED NOT NULL DEFAULT 0,
    subdirs          INT UNSIGNED NOT NULL DEFAULT 0,
    files            BIGINT UNSIGNED NOT NULL,
    bytes            BIGINT UNSIGNED NOT NULL,
    newest_access    INT UNSIGNED NULL,
    oldest_access    INT UNSIGNED NULL,
    cold_1y_bytes    BIGINT UNSIGNED NOT NULL,
    cold_3y_bytes    BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, dir_id),
    KEY by_parent (run_id, parent_dir),
    KEY by_cold (run_id, depth, cold_1y_bytes),
    CONSTRAINT dir_rollup_run FOREIGN KEY (run_id) REFERENCES collector_runs (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
