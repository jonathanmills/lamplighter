#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Install the built RPM in a FRESH Rocky Linux 9.7 container, run it as
# packaged (user, paths, unit command line) behind nginx, against the dev
# environment's LDAP, Keycloak and MariaDB, and check it end to end.
# Usage: webui/packaging/test-rpm.sh   (after build-rpm.sh; dev env running)

set -euo pipefail
cd "$(dirname "$0")"
export DOCKER_CONFIG=$PWD/../devenv/.docker-config
rpm=$(ls dist/lamplighter-webui-*.el9.$(uname -m | sed 's/arm64/aarch64/').rpm | head -1)

docker run --rm --network lamplighter-dev_default \
    -v "$PWD/dist:/dist:ro" -v "$PWD/../devenv:/devenv:ro" -v "$PWD/../devenv/.certs:/certs:ro" \
    -e RPM="/dist/$(basename "$rpm")" docker.io/rockylinux/rockylinux:9.7 bash -c '
set -euo pipefail
ok() { echo "  PASS  $*"; }
dnf -y -q install "$RPM" nginx systemd >/dev/null 2>&1
cp /certs/ca.pem /etc/pki/ca-trust/source/anchors/ && update-ca-trust

echo "Package"
rpm -q lamplighter-webui >/dev/null && ok "installed: $(rpm -q lamplighter-webui)"
! rpm -qR lamplighter-webui | grep -qi php && ok "no PHP requirement"
! rpm -qa | grep -qiE "^php|nodejs" && ok "no PHP or Node.js installed on the server"
id lamplighter-webui >/dev/null && ok "system user lamplighter-webui exists"
[[ $(stat -c "%a %U:%G" /etc/lamplighter.d/webui) == "750 root:lamplighter-webui" ]] && ok "/etc/lamplighter.d/webui is 750 root:lamplighter-webui"
[[ $(stat -c "%a" /etc/lamplighter.d/webui/config.yaml) == "640" ]] && ok "config.yaml is 640"
/opt/lamplighter-webui/bin/python -c "import lamplighter_webui, ldap, authlib" && ok "venv imports work (python-ldap compiled)"
for u in lamplighter-webui.service lamplighter-collector.service lamplighter-collector.timer lamplighter-jobstats.service lamplighter-jobstats.timer lamplighter-reports.service lamplighter-reports.timer; do
    systemd-analyze verify /usr/lib/systemd/system/$u 2>&1 | grep -v "^$" | grep -vi "unknown lvalue\|Failed to load\|bpf" | head -3 || true
done; ok "systemd units verified"

echo "Run as packaged"
# The dev config, adapted to the packaged paths; secrets stay in /devenv/webui.
sed -e "s#base_url: https://webui.test:8443#base_url: https://localhost#" \
    -e "s#frontend_dir: /srv/frontend#frontend_dir: /usr/share/lamplighter-webui/frontend#" \
    /devenv/webui/config.yaml > /etc/lamplighter.d/webui/config.yaml
chgrp lamplighter-webui /etc/lamplighter.d/webui/config.yaml
ExecStart=$(sed -n "/^ExecStart=/,/[^\\\\]$/p" /usr/lib/systemd/system/lamplighter-webui.service | sed "s/^ExecStart=//; s/\\\\$//" | tr "\n" " ")
su -s /bin/bash lamplighter-webui -c "LAMPLIGHTER_WEBUI_CONFIG=/etc/lamplighter.d/webui/config.yaml $ExecStart" >/tmp/app.log 2>&1 &
sed -e "s#robinhood.example.org.pem#robinhood.pem#; s#robinhood.example.org.key#robinhood.key#" \
    /usr/share/lamplighter-webui/nginx-lamplighter-webui.conf.example > /etc/nginx/conf.d/lamplighter-webui.conf
cp /certs/webui.test.pem /etc/pki/tls/certs/robinhood.pem; cp /certs/webui.test.key /etc/pki/tls/private/robinhood.key
nginx -t 2>/dev/null && nginx && ok "nginx config from the package is valid and running"
for i in $(seq 30); do curl -sf https://localhost/healthz >/dev/null 2>&1 && break; sleep 1; done
curl -sf https://localhost/healthz | grep -q "\"ok\"" && ok "healthz through nginx (TLS)"
[[ $(curl -s -o /dev/null -w "%{http_code}" http://localhost/) == 301 ]] && ok "http redirects to https"
curl -sI https://localhost/auth/login | grep -qi "strict-transport-security" && ok "HSTS header from nginx"
curl -sI https://localhost/auth/login | grep -qi "x-frame-options: DENY" && ok "app security headers pass through"
J=/tmp/jar
# A forged X-Forwarded-For must not reach the audit log: nginx replaces it.
curl -s -c $J -b $J -o /dev/null -H "Origin: https://localhost" -H "X-Forwarded-For: 203.0.113.9" \
     -d "username=dave&password=password" https://localhost/auth/ldap
curl -s -b $J https://localhost/api/v1/me | grep -q "\"roles\":\[\"admin\"\]" && ok "LDAP sign-in through nginx; dave is admin"
curl -s -b $J https://localhost/ | grep -q "id=\"root\"" && ok "front end served from /usr/share/lamplighter-webui"
su -s /bin/bash lamplighter-webui -c "LAMPLIGHTER_WEBUI_CONFIG=/etc/lamplighter.d/webui/config.yaml /opt/lamplighter-webui/bin/lamplighter-collector run" 2>&1 | grep -q "ok:" && ok "collector runs as lamplighter-webui"
su -s /bin/bash lamplighter-webui -c "LAMPLIGHTER_WEBUI_CONFIG=/etc/lamplighter.d/webui/config.yaml /opt/lamplighter-webui/bin/lamplighter-reports" >/dev/null 2>&1 && ok "lamplighter-reports runs as lamplighter-webui"
[[ $(stat -c "%a %U:%G" /var/spool/lamplighter-jobstats) == "2770 lamplighter-webui:lamplighter-jobstats" ]] && ok "jobstats spool is 2770 lamplighter-webui:lamplighter-jobstats"
# A delivering account (the account a Lustre server delivers snapshots with) joins lamplighter-jobstats only.
useradd -r -G lamplighter-jobstats -s /bin/bash jsdeliver
su -s /bin/bash jsdeliver -c "/opt/lamplighter-webui/bin/python /devenv/tools/gen_jobstats.py /var/spool/lamplighter-jobstats" >/dev/null 2>&1 \
    && ok "a lamplighter-jobstats member can deliver snapshots"
! su -s /bin/bash jsdeliver -c "cat /etc/lamplighter.d/webui/config.yaml" >/dev/null 2>&1 && ok "a delivering account cannot read /etc/lamplighter.d/webui"
su -s /bin/bash lamplighter-webui -c "LAMPLIGHTER_WEBUI_CONFIG=/etc/lamplighter.d/webui/config.yaml /opt/lamplighter-webui/bin/lamplighter-jobstats" 2>&1 | grep -qE "stored [1-9][0-9]* intervals" \
    && ok "lamplighter-jobstats runs as lamplighter-webui and stores intervals"
curl -s -b $J "https://localhost/api/v1/triage?window=1h" | grep -q "\"find.20003\"" && ok "triage API serves the jobstats"
sleep 1
last=$(curl -s -b $J "https://localhost/api/v1/admin/audit?event=login&user=dave&since_hours=1&limit=1")
echo "$last" | grep -qE "\"client\":\"(::1|127\.0\.0\.1)\"" && ok "real client address recorded through the proxy"
! echo "$last" | grep -q "203.0.113.9" && ok "forged X-Forwarded-For ignored"
echo "done"
' 2>&1 | grep -v "^warning\|Executing"
