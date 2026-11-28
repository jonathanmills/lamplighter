-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 Jonathan Mills
--
-- lamplighter_webui schema, version 6: jobstats stored as summaries.
--
-- Version 4 kept one row per job, per Lustre target, per snapshot: tens of
-- millions of rows a day on a large filesystem, all read by every triage
-- page. lamplighter-jobstats now adds each snapshot's figures to totals per
-- job and per target, by minute (kept 25 hours: the longest triage window
-- plus one hour) and by hour (kept collector.jobstats.retention_hours).
-- A triage window reads whole hours from the hourly tables and the partial
-- first hour from the minute tables. jobstats_presence records which job
-- touched which target in each hour, and when last, so distinct counts
-- ("targets per job", "jobs per target") stay exact for any window.
--
-- Upgrading discards the stored jobstats history (at most retention_hours
-- of it); the triage view fills up again within minutes.

DROP TABLE IF EXISTS jobstats_interval;

CREATE TABLE jobstats_job_minute (
    bucket       INT UNSIGNED NOT NULL,        -- unix time, start of the minute
    job_id       VARCHAR(255) NOT NULL,
    md_ops       BIGINT UNSIGNED NOT NULL DEFAULT 0,
    read_bytes   BIGINT UNSIGNED NOT NULL DEFAULT 0,
    write_bytes  BIGINT UNSIGNED NOT NULL DEFAULT 0,
    read_ops     BIGINT UNSIGNED NOT NULL DEFAULT 0,
    write_ops    BIGINT UNSIGNED NOT NULL DEFAULT 0,
    other_ops    BIGINT UNSIGNED NOT NULL DEFAULT 0,
    op_counts    TEXT NULL,                    -- JSON: requests per operation
    PRIMARY KEY (bucket, job_id),
    KEY by_job (job_id, bucket)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE jobstats_job_hour LIKE jobstats_job_minute;

CREATE TABLE jobstats_target_minute (
    bucket       INT UNSIGNED NOT NULL,
    target       VARCHAR(64) NOT NULL,
    md_ops       BIGINT UNSIGNED NOT NULL DEFAULT 0,
    read_bytes   BIGINT UNSIGNED NOT NULL DEFAULT 0,
    write_bytes  BIGINT UNSIGNED NOT NULL DEFAULT 0,
    read_ops     BIGINT UNSIGNED NOT NULL DEFAULT 0,
    write_ops    BIGINT UNSIGNED NOT NULL DEFAULT 0,
    other_ops    BIGINT UNSIGNED NOT NULL DEFAULT 0,
    last_end     INT UNSIGNED NOT NULL DEFAULT 0,  -- newest snapshot time in this minute
    PRIMARY KEY (bucket, target)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE jobstats_target_hour LIKE jobstats_target_minute;

CREATE TABLE jobstats_presence (
    hour         INT UNSIGNED NOT NULL,        -- unix time, start of the hour
    job_id       VARCHAR(255) NOT NULL,
    target       VARCHAR(64) NOT NULL,
    last_seen    INT UNSIGNED NOT NULL,        -- newest snapshot time with activity
    PRIMARY KEY (hour, job_id, target),
    KEY by_job (job_id, hour),
    KEY by_target (target, job_id, hour, last_seen)    -- jobs per target, counted in index order
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Filesystem-wide totals per minute, for the time series and the totals.
CREATE TABLE jobstats_total_minute (
    bucket       INT UNSIGNED NOT NULL PRIMARY KEY,
    md_ops       BIGINT UNSIGNED NOT NULL DEFAULT 0,
    read_bytes   BIGINT UNSIGNED NOT NULL DEFAULT 0,
    write_bytes  BIGINT UNSIGNED NOT NULL DEFAULT 0,
    read_ops     BIGINT UNSIGNED NOT NULL DEFAULT 0,
    write_ops    BIGINT UNSIGNED NOT NULL DEFAULT 0,
    other_ops    BIGINT UNSIGNED NOT NULL DEFAULT 0,
    last_end     INT UNSIGNED NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
