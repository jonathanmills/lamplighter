#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Trust the dev test CA system-wide, then run the given command.

set -euo pipefail

if [[ -f /certs/ca.pem ]]; then
    cp /certs/ca.pem /etc/pki/ca-trust/source/anchors/lux-dev-ca.pem
    update-ca-trust
fi

exec "$@"
