-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 Jonathan Mills
--
-- lux_webui schema, version 1: the collector's outputs.
-- This schema belongs to the web UI; Robinhood's own schema is never written.

CREATE TABLE collector_runs (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    started_at    DATETIME(3) NOT NULL,
    finished_at   DATETIME(3) NULL,
    status        ENUM('running', 'ok', 'failed') NOT NULL,
    entries       BIGINT UNSIGNED NULL,     -- files covered by the age histogram
    error         TEXT NULL,
    KEY by_time (status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Copies of Robinhood's ACCT_STAT, one per run: usage history over time.
CREATE TABLE acct_history (
    run_id   BIGINT UNSIGNED NOT NULL,
    uid      VARCHAR(127) NOT NULL,
    gid      VARCHAR(127) NOT NULL,
    type     VARCHAR(16) NOT NULL,
    entries  BIGINT UNSIGNED NOT NULL,
    bytes    BIGINT UNSIGNED NOT NULL,
    blocks   BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, uid, gid, type),
    CONSTRAINT acct_history_run FOREIGN KEY (run_id) REFERENCES collector_runs (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Regular files per owner and group, by age band of last access and of
-- last modification (joint distribution). Bands, see collector.AGE_BANDS:
-- 0 < 7 d, 1 7-30 d, 2 30-90 d, 3 90 d-1 y, 4 1-3 y, 5 > 3 y.
CREATE TABLE age_hist (
    run_id         BIGINT UNSIGNED NOT NULL,
    uid            VARCHAR(127) NOT NULL,
    gid            VARCHAR(127) NOT NULL,
    access_band    TINYINT UNSIGNED NOT NULL,
    modify_band    TINYINT UNSIGNED NOT NULL,
    files          BIGINT UNSIGNED NOT NULL,
    bytes          BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, uid, gid, access_band, modify_band),
    CONSTRAINT age_hist_run FOREIGN KEY (run_id) REFERENCES collector_runs (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
