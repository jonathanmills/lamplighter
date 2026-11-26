#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Trust the dev test CA, and create local Unix accounts matching the
# LDAP test users so Robinhood resolves uid/gid numbers to names.

set -euo pipefail

if [[ -f /certs/ca.pem ]]; then
    cp /certs/ca.pem /etc/pki/ca-trust/source/anchors/rbh-dev-ca.pem
    update-ca-trust
fi

if [[ -f /devenv/ldap/data.ldif ]]; then
    python3.12 /devenv/testdata/accounts.py /devenv/ldap/data.ldif
fi

exec "$@"
