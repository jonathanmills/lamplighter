-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 Jonathan Mills
--
-- lamplighter_webui schema, version 2: audit events, searchable from the UI.
-- journald (the "lamplighter_webui.audit" logger) remains the authoritative copy;
-- this table is a convenience copy with a retention limit.

CREATE TABLE audit_log (
    id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    ts        DATETIME(3) NOT NULL,      -- UTC
    event     VARCHAR(64) NOT NULL,
    outcome   VARCHAR(16) NOT NULL,
    user      VARCHAR(255) NULL,
    client    VARCHAR(64) NULL,
    details   TEXT NULL,               -- JSON object of the remaining fields
    KEY by_time (ts),
    KEY by_user (user, ts),
    KEY by_event (event, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
