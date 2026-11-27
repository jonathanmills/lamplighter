#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# First start: build cn=config offline, start slapd, then load the data
# *online* so the memberOf overlay fills in users' memberOf attributes.

set -euo pipefail

conf=/etc/ldap/slapd.d
mkdir -p /run/slapd
chown openldap:openldap /run/slapd

first_start=no
if [[ -z "$(ls -A "$conf")" ]]; then
    slapadd -n0 -F "$conf" -l /opt/ldap/config.ldif
    chown -R openldap:openldap "$conf" /var/lib/ldap
    first_start=yes
fi

slapd -h "ldap:/// ldaps:/// ldapi:///" -u openldap -g openldap \
      -F "$conf" -d 0 &
pid=$!
trap 'kill -TERM $pid; wait $pid' TERM INT

for _ in $(seq 50); do
    ldapsearch -Q -Y EXTERNAL -H ldapi:/// -b "" -s base >/dev/null 2>&1 && break
    sleep 0.2
done

if [[ $first_start == yes ]]; then
    ldapadd -Q -Y EXTERNAL -H ldapi:/// -f /opt/ldap/data.ldif >/dev/null
    echo "lamplighter-dev-ldap: test data loaded"
fi
echo "lamplighter-dev-ldap: ready"

wait $pid
