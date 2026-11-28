#!/bin/bash
# SPDX-License-Identifier: CECILL-C
# Copyright (C) 2026 Jonathan Mills
#
# Data for the side-by-side tests: the generated test tree in /data (the
# workbench's tmpfs, so it is regenerated after a restart) scanned by the
# C reference engine into its own database, robinhood_difftest. Both sides
# of every comparison then read the same database. Runs in the workbench.
#
# Usage: setup.sh REFERENCE_DIR

set -euo pipefail
ref=$1
here=$(cd "$(dirname "$0")" && pwd)
db=robinhood_difftest

# a fresh database every run (only this test database, never robinhood_dev)
mysql -h mariadb -u root -pdevroot -e \
    "DROP DATABASE IF EXISTS $db; CREATE DATABASE $db; GRANT ALL PRIVILEGES ON $db.* TO 'robinhood'@'%';" 2>/dev/null

if [[ -z $(ls -A /data 2>/dev/null) ]]; then
    echo "generating the test tree in /data"
    python3.12 /devenv/testdata/generate.py /data /devenv/ldap/data.ldif >/dev/null
fi

echo "scanning /data into $db with the reference engine"
LD_LIBRARY_PATH=$ref/src/modules/.libs \
    "$ref/src/robinhood/lamplighter" --scan --once --alter-db -f "$here/difftest.conf" >/dev/null 2>&1
LD_LIBRARY_PATH=$ref/src/modules/.libs \
    "$ref/src/robinhood/lamplighter" --run=alert --target=all --once -f "$here/difftest.conf" >/dev/null 2>&1 || true
n=$(mysql -h mariadb -u root -pdevroot -N "$db" -e "SELECT COUNT(*) FROM ENTRIES" 2>/dev/null)
echo "$db: $n entries"

# The policy includes the test suite builds for each PURPOSE
# (tests/test_suite/2-run-tests.sh), for loading its configurations with
# their policies (stage 2a.3 on)
env=/build/difftest-env
tpl=$here/../../doc/templates/includes
mkdir -p "$env"
sed -e "s/cleanup/purge/" "$tpl/tmpfs.inc" > "$env/policies-tmpfs.inc"
sed -e "s/lhsm_archive/migration/" -e "s/lhsm_release/purge/" -e "s/lhsm_remove/hsm_remove/" \
    "$tpl/lhsm.inc" > "$env/policies-lhsm.inc"
sed -e "s/backup_archive/migration/" -e "s/backup_remove/hsm_remove/" "$tpl/backup.inc" > "$env/policies-backup.inc"
cat >> "$env/policies-backup.inc" <<'INC'
define_policy purge {
    scope { type != directory }
    status_manager = none;
    default_action = common.unlink;
    default_lru_sort_attr = none;
}
INC
sed -e "s/shook_archive/migration/" -e "s/shook_release/purge/" -e "s/shook_remove/hsm_remove/" \
    "$tpl/shook.inc" > "$env/policies-shook.inc"
