#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Robinhood web UI development environment. Run from macOS (Podman or
# Docker) or Linux. See README.md.

set -euo pipefail
cd "$(dirname "$0")"

# Use a private, empty Docker client config so leftovers in
# ~/.docker/config.json (such as a Docker Desktop credential helper)
# cannot break builds. Only public images are pulled.
export DOCKER_CONFIG="$PWD/.docker-config"
mkdir -p "$DOCKER_CONFIG"
[[ -f $DOCKER_CONFIG/config.json ]] || echo '{}' > "$DOCKER_CONFIG/config.json"

compose() { docker compose "$@"; }
wb() { compose exec -T -e PYTHONDONTWRITEBYTECODE=1 workbench "$@"; }

wait_keycloak() {
    for _ in $(seq 90); do
        if wb curl -sf --cacert /certs/ca.pem \
               https://keycloak.test:9443/realms/rbh >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    echo "keycloak did not become ready; see: ./devenv.sh logs keycloak" >&2
    return 1
}

usage() {
    cat <<'EOF'
usage: ./devenv.sh COMMAND

  up        create test certificates, build images, start all services
  build     build Robinhood (POSIX mode) from the repository
  data      (re)generate the sample file tree   [--scale N]
  scan      run a full Robinhood scan of the sample tree into MariaDB
  test      run the smoke test (LDAP, Keycloak, MariaDB/Robinhood)
  webui-test [ARGS]  run the web UI back end's pytest suite (unit + integration)
  shots     render pages in headless Chromium; screenshots go to .shots/
  all       up + build + data + scan + test + webui-test
  sql       open a MariaDB shell on the Robinhood database
  shell     open a shell in the Rocky 9.7 workbench
  logs [S]  follow service logs
  status    show service status
  down      stop everything (keeps the database and build volumes)
  reset     stop everything and delete all volumes and certificates
EOF
}

cmd=${1:-help}; shift || true
case $cmd in
    up)
        ./gen-certs.sh
        mkdir -p ../frontend/dist
        compose up -d --build
        wait_keycloak
        compose ps
        ;;
    build)
        wb /devenv/robinhood/build.sh
        ;;
    data)
        wb python3.12 /devenv/testdata/generate.py /data /devenv/ldap/data.ldif "$@"
        ;;
    scan)
        wb env LD_LIBRARY_PATH=/build/robinhood/src/modules/.libs \
            /build/robinhood/src/robinhood/robinhood --scan --once \
            -f /devenv/robinhood/robinhood.conf
        wb mysql -h mariadb -u robinhood -probinhood robinhood_dev -e \
            "SELECT COUNT(*) AS entries FROM ENTRIES; SELECT uid, type, count, size FROM ACCT_STAT ORDER BY size DESC;"
        ;;
    test)
        wait_keycloak
        wb bash /devenv/smoke-test.sh
        ;;
    webui-test)
        wait_keycloak
        compose exec -T webui pytest -q -p no:cacheprovider tests "$@"
        ;;
    shots)
        docker run --rm --network rbh-dev_default --ipc=host -v "$PWD:/devenv" \
            mcr.microsoft.com/playwright/python:v1.48.0-noble \
            sh -c "pip install -q playwright==1.48.0 && python /devenv/tools/screenshot.py"
        ;;
    all)
        "$0" up && "$0" build && "$0" data && "$0" scan && "$0" test && "$0" webui-test
        ;;
    sql)
        compose exec workbench mysql -h mariadb -u robinhood -probinhood robinhood_dev
        ;;
    shell)
        compose exec workbench bash
        ;;
    logs)
        compose logs -f "$@"
        ;;
    status)
        compose ps
        ;;
    down)
        compose down
        ;;
    reset)
        compose down -v
        rm -rf .certs
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        usage >&2; exit 2
        ;;
esac
