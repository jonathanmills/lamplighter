# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
#
# Dev OpenLDAP server: RFC 2307bis schema, memberOf overlay, TLS only.

FROM docker.io/library/debian:bookworm-slim

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        slapd ldap-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/* /etc/ldap/slapd.d/* /var/lib/ldap/*

COPY rfc2307bis.ldif config.ldif data.ldif entrypoint.sh /opt/ldap/
RUN chmod 755 /opt/ldap/entrypoint.sh

EXPOSE 389 636
ENTRYPOINT ["/opt/ldap/entrypoint.sh"]
