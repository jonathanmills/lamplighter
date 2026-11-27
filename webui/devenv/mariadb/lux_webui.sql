-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 Jonathan Mills
--
-- Dev only: the web UI's own schema and user. TEST-ONLY password.
-- Runs on a fresh MariaDB volume (docker-entrypoint-initdb.d) and on
-- every "devenv.sh up" (idempotent).
--
-- In production: the lux_webui user gets SELECT on Robinhood's schema
-- (ideally on a replica) and full rights on its own lux_webui schema only.

CREATE DATABASE IF NOT EXISTS lux_webui CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'lux_webui'@'%' IDENTIFIED BY 'lux_webui';
GRANT SELECT ON robinhood_dev.* TO 'lux_webui'@'%';
GRANT ALL PRIVILEGES ON lux_webui.* TO 'lux_webui'@'%';
FLUSH PRIVILEGES;
