-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 Jonathan Mills
--
-- Dev only: the web UI's own schema and user. TEST-ONLY password.
-- Runs on a fresh MariaDB volume (docker-entrypoint-initdb.d) and on
-- every "devenv.sh up" (idempotent).
--
-- In production: the rbh_webui user gets SELECT on Robinhood's schema
-- (ideally on a replica) and full rights on its own rbh_webui schema only.

CREATE DATABASE IF NOT EXISTS rbh_webui CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'rbh_webui'@'%' IDENTIFIED BY 'rbh_webui';
GRANT SELECT ON robinhood_dev.* TO 'rbh_webui'@'%';
GRANT ALL PRIVILEGES ON rbh_webui.* TO 'rbh_webui'@'%';
FLUSH PRIVILEGES;
