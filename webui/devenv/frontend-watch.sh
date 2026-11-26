#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Rebuild the front end whenever a source file changes. This polls
# file modification times instead of waiting for change events, because
# events from a macOS host don't reach containers inside the Podman (or
# Docker Desktop) Linux VM.

set -u
cd /app
npm ci --no-audit --no-fund

stamp() {
    find src public index.html vite.config.ts tsconfig.json package.json \
         -type f -exec stat -c '%Y %s %n' {} + 2>/dev/null | md5sum
}

last=""
while true; do
    now=$(stamp)
    if [ "$now" != "$last" ]; then
        last=$now
        echo "frontend: change detected, building at $(date +%T)"
        npm run build --silent || echo "frontend: BUILD FAILED (fix the error above; still watching)"
    fi
    sleep 1
done
