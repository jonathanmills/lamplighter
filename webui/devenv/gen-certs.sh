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
    # Full CA extensions: strict verifiers (Python 3.13, used by Checkmk 2.5,
    # sets VERIFY_X509_STRICT) reject certificates without them.
    printf 'basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n' > ca.ext
    openssl req -new -newkey rsa:3072 -nodes -sha256 \
        -subj "/O=Lamplighter Dev/CN=Lamplighter Dev Test CA" \
        -keyout ca.key -out ca.csr 2>/dev/null
    openssl x509 -req -in ca.csr -signkey ca.key -days 3650 -sha256 \
        -extfile ca.ext -out ca.pem 2>/dev/null
    rm -f ca.csr ca.ext
    echo "created test CA: $(pwd)/ca.pem"
fi

# name: extra SANs
issue() {
    local name=$1 sans=$2
    [[ -f $name.pem ]] && return 0
    openssl req -newkey rsa:2048 -nodes -sha256 \
        -subj "/O=Lamplighter Dev/CN=$name" \
        -keyout "$name.key" -out "$name.csr" 2>/dev/null
    printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\nbasicConstraints=critical,CA:FALSE\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n' \
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
