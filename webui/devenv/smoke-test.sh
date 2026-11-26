#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Smoke test of the dev environment, run inside the workbench by
# "./devenv.sh test". Every TLS connection verifies the test CA.

set -uo pipefail

pass=0 fail=0
check() {
    local name=$1; shift
    if out=$("$@" 2>&1); then
        echo "  PASS  $name"; pass=$((pass + 1))
    else
        echo "  FAIL  $name"; echo "$out" | sed 's/^/        /' | head -5
        fail=$((fail + 1))
    fi
}
expect_fail() {
    local name=$1; shift
    if "$@" >/dev/null 2>&1; then
        echo "  FAIL  $name (unexpectedly succeeded)"; fail=$((fail + 1))
    else
        echo "  PASS  $name"; pass=$((pass + 1))
    fi
}

export LDAPTLS_CACERT=/certs/ca.pem
BIND=(-D cn=rbh-webui,ou=services,dc=rbh,dc=test -w webui-bind-secret)
KC=https://keycloak.test:9443/realms/rbh
CA=(--cacert /certs/ca.pem)

# jwt_true TOKEN EXPR: succeed if the Python EXPR is true for the JWT's
# claims, available as the dict "c". The signature is not checked here;
# the web UI back end will verify it against the realm's JWKS.
jwt_true() {
    python3.12 - "$1" "$2" <<'PY'
import base64, json, sys
part = sys.argv[1].split(".")[1]
c = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
sys.exit(0 if eval(sys.argv[2], {}, {"c": c}) else 1)
PY
}

echo "LDAP (ldap.test)"
check "LDAPS bind as service account" \
    ldapwhoami -H ldaps://ldap.test "${BIND[@]}"
check "StartTLS bind as service account" \
    ldapwhoami -ZZ -H ldap://ldap.test "${BIND[@]}"
check "alice has memberOf cn=rbh-viewers" bash -c \
    "ldapsearch -LLL -H ldaps://ldap.test ${BIND[*]} -b ou=people,dc=rbh,dc=test '(uid=alice)' memberOf | grep -q 'memberOf: cn=rbh-viewers,'"
check "groupOfNames search by member" bash -c \
    "ldapsearch -LLL -H ldaps://ldap.test ${BIND[*]} -b ou=groups,dc=rbh,dc=test '(&(objectClass=groupOfNames)(member=uid=carol,ou=people,dc=rbh,dc=test))' cn | grep -q 'cn: rbh-data-managers'"
expect_fail "plain LDAP without TLS is refused" \
    ldapwhoami -H ldap://ldap.test "${BIND[@]}"
# A freshly made CA that never signed anything here: must not be trusted.
other_ca=$(mktemp -d)
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=Unrelated CA" \
    -keyout "$other_ca/key.pem" -out "$other_ca/ca.pem" 2>/dev/null
expect_fail "server cert from another CA is rejected" \
    env LDAPTLS_CACERT="$other_ca/ca.pem" ldapwhoami -H ldaps://ldap.test "${BIND[@]}"
expect_fail "wrong user password is rejected" \
    ldapwhoami -H ldaps://ldap.test -D uid=carol,ou=people,dc=rbh,dc=test -w wrong

echo "Keycloak (keycloak.test)"
expect_fail "Keycloak cert from another CA is rejected" \
    curl -sf --cacert "$other_ca/ca.pem" "$KC/.well-known/openid-configuration"
check "OIDC discovery over TLS" bash -c \
    "curl -sf ${CA[*]} $KC/.well-known/openid-configuration | jq -e '.issuer == \"$KC\"'"
tok=$(curl -sf "${CA[@]}" "$KC/protocol/openid-connect/token" \
      -d grant_type=client_credentials -d client_id=rbh-checkmk \
      -d client_secret=dev-checkmk-client-secret | jq -r .access_token)
check "Checkmk client-credentials token" test -n "$tok" -a "$tok" != null
check "token audience includes rbh-webui" \
    jwt_true "$tok" '"rbh-webui" in ([c["aud"]] if isinstance(c["aud"], str) else c["aud"])'
check "token carries rbh-webui role 'monitoring'" \
    jwt_true "$tok" '"monitoring" in c["resource_access"]["rbh-webui"]["roles"]'
utok=$(curl -sf "${CA[@]}" "$KC/protocol/openid-connect/token" \
       -d grant_type=password -d client_id=rbh-webui \
       -d client_secret=dev-webui-client-secret -d scope=openid \
       -d username=carol -d password=password | jq -r .access_token)
check "LDAP user carol logs in through Keycloak" test -n "$utok" -a "$utok" != null
check "carol's preferred_username equals LDAP uid" \
    jwt_true "$utok" 'c.get("preferred_username") == "carol"'
expect_fail "wrong password via Keycloak is rejected" \
    curl -sf "${CA[@]}" "$KC/protocol/openid-connect/token" \
         -d grant_type=password -d client_id=rbh-webui \
         -d client_secret=dev-webui-client-secret -d username=carol -d password=wrong

echo "MariaDB + Robinhood (robinhood_dev)"
SQL=(mysql -h mariadb -u robinhood -probinhood -N robinhood_dev -e)
check "database reachable" "${SQL[@]}" "SELECT 1"
check "ENTRIES populated by a scan" bash -c \
    "test \$(${SQL[*]} 'SELECT COUNT(*) FROM ENTRIES') -gt 1000"
check "ACCT_STAT has per-user rows" bash -c \
    "${SQL[*]} 'SELECT DISTINCT uid FROM ACCT_STAT' | grep -q frank"
check "VARS has scan bookkeeping" bash -c \
    "test \$(${SQL[*]} 'SELECT COUNT(*) FROM VARS') -gt 0"

rm -rf "$other_ca"
echo
echo "$pass passed, $fail failed"
[[ $fail -eq 0 ]]
