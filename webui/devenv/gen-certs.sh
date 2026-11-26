#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Create a throwaway test CA and server certificates for the dev
# environment. Output goes to webui/devenv/.certs/ (git-ignored).
# Safe to re-run: existing files are kept unless --force is given.

set -euo pipefail

cd "$(dirname "$0")"
out=.certs
days=825

if [[ "${1:-}" == "--force" ]]; then
    rm -rf "$out"
fi
mkdir -p "$out"
cd "$out"

if [[ ! -f ca.pem ]]; then
    openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
        -subj "/O=Robinhood Dev/CN=Robinhood Dev Test CA" \
        -keyout ca.key -out ca.pem 2>/dev/null
    echo "created test CA: $(pwd)/ca.pem"
fi

# name: extra SANs
issue() {
    local name=$1 sans=$2
    [[ -f $name.pem ]] && return 0
    openssl req -newkey rsa:2048 -nodes -sha256 \
        -subj "/O=Robinhood Dev/CN=$name" \
        -keyout "$name.key" -out "$name.csr" 2>/dev/null
    printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\n' \
        "$sans" > "$name.ext"
    openssl x509 -req -in "$name.csr" -CA ca.pem -CAkey ca.key \
        -CAcreateserial -days "$days" -sha256 -extfile "$name.ext" \
        -out "$name.pem" 2>/dev/null
    rm -f "$name.csr" "$name.ext"
    echo "issued $name.pem"
}

issue ldap.test     "DNS:ldap.test,DNS:localhost,IP:127.0.0.1"
issue keycloak.test "DNS:keycloak.test,DNS:localhost,IP:127.0.0.1"
issue webui.test    "DNS:webui.test,DNS:localhost,IP:127.0.0.1"

# Containers run services as non-root users: keys must be readable.
# These are test-only keys for a throwaway CA.
chmod 644 ./*.key ./*.pem
