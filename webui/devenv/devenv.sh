#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Lamplighter web UI development environment. Run from macOS (Podman or
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
               https://keycloak.test:9443/realms/lamplighter >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    echo "keycloak did not become ready; see: ./devenv.sh logs keycloak" >&2
    return 1
}

# Checkmk 2.5's plugin APIs and packaging code (GPLv2), copied out of the
# official image for testing and packaging only; never committed. The image
# is x86-64 only: on Apple Silicon it runs emulated, which is fine for copying
# files (a full Checkmk site does not run under emulation).
CMK_IMAGE=docker.io/checkmk/check-mk-community:2.5.0p21
cmk_api() {
    [[ -d .cmk-api/cmk/agent_based/v2 && -d .cmk-api/cmk/mkp_tool ]] && return 0
    mkdir -p .cmk-api
    docker run --rm --platform linux/amd64 --entrypoint sh -v "$PWD/.cmk-api:/out" "$CMK_IMAGE" -c '
        S=$(ls -d /omd/versions/*/lib/python3.*/site-packages | head -1)
        mkdir -p /out/cmk && for d in agent_based rulesets server_side_calls password_store mkp_tool; do
            cp -r "$S/cmk/$d" /out/cmk/; done' 2>&1 | grep -v -e qemu -e "Segmentation fault" || true
}

usage() {
    cat <<'EOF'
usage: ./devenv.sh COMMAND

  up        create test certificates, build images, start all services
  build     build Robinhood (POSIX mode) from the repository
  data      (re)generate the sample file tree   [--scale N]
  scan      run a full Robinhood scan of the sample tree into MariaDB
  classify  run the classifier alert policy once (fileclasses, alert_status)
  collect   run the web UI collector once (age histogram, usage snapshot)
  jobstats  write simulated Lustre jobstats to the spool and run lamplighter-jobstats
  docshots  render the README's screenshot gallery into doc/screenshots
  prometheus       start the optional Prometheus with a simulated jobstats exporter
  prometheus-down  stop it
  test      run the smoke test (LDAP, Keycloak, MariaDB/Robinhood)
  webui-test [ARGS]  run the web UI back end's pytest suite (unit + integration)
  checkmk-test  test the Checkmk plugin (Python 3.13, Checkmk 2.5's real APIs, live dev data)
  mkp       build the Checkmk extension package into ../checkmk/dist/
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
        # the web UI's own schema and user (idempotent; also run on fresh volumes)
        compose exec -T mariadb mariadb -uroot -pdevroot < mariadb/lamplighter_webui.sql
        wait_keycloak
        compose ps
        ;;
    build)
        wb /devenv/engine/build.sh
        ;;
    data)
        wb python3.12 /devenv/testdata/generate.py /data /devenv/ldap/data.ldif "$@"
        ;;
    scan)
        wb env LD_LIBRARY_PATH=/build/lamplighter/src/modules/.libs \
            /build/lamplighter/src/robinhood/lamplighter --scan --once --alter-db \
            -f /devenv/engine/lamplighter.conf
        wb mysql -h mariadb -u robinhood -probinhood robinhood_dev -e \
            "SELECT COUNT(*) AS entries FROM ENTRIES; SELECT uid, type, count, size FROM ACCT_STAT ORDER BY size DESC;"
        ;;
    classify)
        # the classifier alert policy: marks entries alert/clear (alert_status)
        wb env LD_LIBRARY_PATH=/build/lamplighter/src/modules/.libs \
            /build/lamplighter/src/robinhood/lamplighter --run="alert(all)" --once \
            -f /devenv/engine/lamplighter.conf
        ;;
    collect)
        compose exec -T webui lamplighter-collector run
        ;;
    prometheus)
        # opt-in: Prometheus + simulated jobstats exporter (http://127.0.0.1:9090)
        compose --profile prometheus up -d jobstats-exporter prometheus
        ;;
    prometheus-down)
        compose --profile prometheus stop prometheus jobstats-exporter
        ;;
    jobstats)
        # simulated Lustre servers drop snapshots; lamplighter-jobstats reads them.
        # Start from empty tables: each run simulates the last two hours anew.
        compose exec -T webui python - <<'PY'
from sqlalchemy import text
from lamplighter_webui.config import load_settings
from lamplighter_webui.db import make_engine
from lamplighter_webui.store import migrate
engine = make_engine(load_settings().store)
migrate(engine)
with engine.begin() as c:
    for t in ("job_minute", "job_hour", "target_minute", "target_hour", "total_minute", "presence"):
        c.execute(text(f"DELETE FROM jobstats_{t}"))
    c.execute(text("DELETE FROM jobstats_sources"))
PY
        compose exec -T webui python /devenv/tools/gen_jobstats.py /var/spool/lamplighter-jobstats
        compose exec -T webui python -m lamplighter_webui.jobstats
        ;;
    test)
        python3 tools/check_names.py || exit 1   # the naming rule (bin: lux-, else lamplighter)
        wait_keycloak
        wb bash /devenv/smoke-test.sh
        ;;
    webui-test)
        wait_keycloak
        compose exec -T webui pytest -q -p no:cacheprovider tests "$@"
        ;;
    checkmk-test|mkp)
        cmk_api
        run_py313() {
            docker run --rm --network lamplighter-dev_default -v "$PWD/.cmk-api:/cmkapi:ro" \
                -v "$PWD/../checkmk:/plugin" -v "$PWD/.certs:/certs:ro" \
                -e PYTHONPATH=/cmkapi:/plugin -e PYTHONDONTWRITEBYTECODE=1 \
                docker.io/library/python:3.13-slim sh -c "pip install -q pytest pydantic cryptography 2>/dev/null; $1"
        }
        if [[ $cmd == checkmk-test ]]; then
            run_py313 "python -m pytest -q -p no:cacheprovider /plugin/tests"
        else
            mkdir -p ../checkmk/dist
            run_py313 "python /plugin/build_mkp.py /plugin/dist"
        fi
        ;;
    docshots)
        # The README gallery: fresh sample data, then a fixed-size render of
        # a few pages into doc/screenshots (committed).
        "$0" collect >/dev/null && "$0" jobstats >/dev/null || exit 1
        compose exec -T webui python /devenv/tools/seed_reports.py >/dev/null || exit 1
        rm -rf .shots/docs
        # one retry: headless Chromium can stall at start-up
        for attempt in 1 2; do
            docker run --rm --network lamplighter-dev_default --ipc=host -v "$PWD:/devenv" \
                mcr.microsoft.com/playwright/python:v1.48.0-noble \
                sh -c "pip install -q playwright==1.48.0 pillow && timeout 240 python /devenv/tools/screenshot.py --docs" && break
            [[ $attempt == 2 ]] && exit 1
            echo "docshots: attempt 1 failed or timed out; retrying" >&2
        done
        rm -rf ../../doc/screenshots && mkdir -p ../../doc/screenshots && cp .shots/docs/*.webp ../../doc/screenshots/
        ls -la ../../doc/screenshots
        ;;
    shots)
        # demo scheduled reports with a history (the Reports screenshots)
        compose exec -T webui python /devenv/tools/seed_reports.py || exit 1
        # Hard time limit and one retry: headless Chromium can stall at start-up.
        for attempt in 1 2; do
            if docker run --rm --network lamplighter-dev_default --ipc=host -v "$PWD:/devenv" \
                   mcr.microsoft.com/playwright/python:v1.48.0-noble \
                   sh -c "pip install -q playwright==1.48.0 && timeout 240 python /devenv/tools/screenshot.py"; then
                break
            fi
            [[ $attempt == 1 ]] && echo "shots: attempt 1 failed or timed out; retrying" >&2
        done
        ;;
    all)
        "$0" up && "$0" build && "$0" data && "$0" scan && "$0" classify && "$0" collect && "$0" jobstats && "$0" test && "$0" webui-test
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
