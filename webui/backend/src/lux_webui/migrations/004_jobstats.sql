-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 Jonathan Mills
--
-- lux_webui schema, version 4: Lustre jobstats for the triage view.
-- Written by lux-jobstats (spool directory or Prometheus); see
-- doc/webui-design.md section 5.4.

-- One row per source: a Lustre server's spool name, or the Prometheus URL.
CREATE TABLE jobstats_sources (
    source         VARCHAR(255) NOT NULL PRIMARY KEY,
    last_snapshot  INT UNSIGNED NULL,          -- unix time of the newest snapshot used
    targets        TEXT NULL,                  -- comma-separated Lustre targets
    jobs           INT UNSIGNED NOT NULL DEFAULT 0,
    last_error     TEXT NULL,
    checked_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Activity of one job on one target between two snapshots.
CREATE TABLE jobstats_interval (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source       VARCHAR(255) NOT NULL,
    target       VARCHAR(64) NOT NULL,
    job_id       VARCHAR(255) NOT NULL,
    t_start      INT UNSIGNED NOT NULL,
    t_end        INT UNSIGNED NOT NULL,
    md_ops       BIGINT UNSIGNED NOT NULL DEFAULT 0,
    read_bytes   BIGINT UNSIGNED NOT NULL DEFAULT 0,
    write_bytes  BIGINT UNSIGNED NOT NULL DEFAULT 0,
    read_ops     BIGINT UNSIGNED NOT NULL DEFAULT 0,
    write_ops    BIGINT UNSIGNED NOT NULL DEFAULT 0,
    other_ops    BIGINT UNSIGNED NOT NULL DEFAULT 0,
    op_counts    TEXT NULL,                  -- JSON: requests per operation
    KEY by_end (t_end),
    KEY by_job (job_id, t_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
