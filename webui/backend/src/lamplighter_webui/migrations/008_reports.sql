-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 Jonathan Mills
--
-- lamplighter_webui schema, version 8: scheduled reports (reports.py), the
-- equivalent of the old PHP GUI's "tasks" and custom graphs. A report is
-- one of the web UI's exportable tables with saved parameters, run on a
-- schedule by lamplighter-reports as its owner. Each run keeps the table.

CREATE TABLE reports (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name         VARCHAR(100) NOT NULL,
    owner        VARCHAR(127) NOT NULL,          -- directory uid: runs with their permissions
    source       VARCHAR(64) NOT NULL,           -- reports.SOURCES key
    params       TEXT NOT NULL,                  -- JSON, validated against the source
    schedule     ENUM('manual', 'hourly', 'daily', 'weekly') NOT NULL DEFAULT 'daily',
    keep_runs    INT UNSIGNED NOT NULL DEFAULT 1,  -- 1 = latest only, more = history
    recipients   TEXT NULL,                      -- JSON list of directory uids (email)
    chart        TEXT NULL,                      -- JSON: {"value": col, "stack_by": col|null, "top": n}
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_run_at  INT UNSIGNED NULL,              -- unix time of the last run started
    UNIQUE KEY by_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE report_runs (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    report_id    BIGINT UNSIGNED NOT NULL,
    started_at   INT UNSIGNED NOT NULL,
    seconds      DECIMAL(9, 2) NULL,
    status       ENUM('ok', 'error', 'denied') NOT NULL,
    trigger_by   VARCHAR(127) NOT NULL,          -- 'schedule' or the uid who pressed "Run now"
    row_count    INT UNSIGNED NOT NULL DEFAULT 0,
    message      TEXT NULL,                      -- error, denial or email outcome
    data         LONGTEXT NULL,                  -- JSON {"columns": [...], "rows": [...]}
    KEY by_report (report_id, started_at),
    CONSTRAINT report_runs_report FOREIGN KEY (report_id) REFERENCES reports (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
