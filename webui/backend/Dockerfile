# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Development image for the web UI back end (Rocky Linux 9.7, Python
# 3.12). The source tree is mounted at /app at runtime for live reload.

FROM docker.io/rockylinux/rockylinux:9.7

RUN dnf -y install python3.12 python3.12-pip python3.12-devel gcc \
        openldap-devel openldap-clients \
 && dnf clean all \
 && python3.12 -m venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e '.[dev]'

COPY entrypoint.sh /usr/local/bin/lamplighter-webui-entrypoint
ENTRYPOINT ["/usr/local/bin/lamplighter-webui-entrypoint"]
CMD ["uvicorn", "lamplighter_webui.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8443", \
     "--ssl-certfile", "/certs/webui.test.pem", "--ssl-keyfile", "/certs/webui.test.key", \
     "--reload", "--reload-dir", "/app/src"]
