# Installing the Lamplighter web UI (Rocky Linux / RHEL 9)

The `lamplighter-webui` RPM installs:

| Path | What |
|---|---|
| `/opt/lux-webui/` | The back end in its own Python 3.12 virtual environment. No system Python packages are touched. |
| `/usr/share/lux-webui/frontend/` | The built front end (static files, served by the back end) |
| `/etc/lux-webui/config.yaml` | Configuration (`root:lux-webui`, mode 0640); a commented example on first install |
| `lux-webui.service` | The web service: 127.0.0.1:8000, as user `lux-webui`, sandboxed by systemd |
| `lux-collector.service` / `.timer` | The collector, nightly at 02:30 (±30 min) |
| `/usr/share/lux-webui/*.example` | Templates: `nginx-lux-webui.conf.example` and a pristine `config.yaml.example` |
| `/usr/share/doc/lamplighter-webui/` | This file |

No PHP, no Node.js and no compiler is needed on the server. The PHP GUI (`robinhood-webgui`) is no longer built.

## Steps

1. **Install:** `dnf install ./lamplighter-webui-0.1.0-1.el9.<arch>.rpm nginx`
2. **Databases:** let your DBA create:
   - on the Robinhood database (preferably a replica): user `lux_webui` with `SELECT` and `CREATE TEMPORARY TABLES` on the Robinhood schema
   - a schema `lux_webui` with `ALL` for that user (it can live on another server)
3. **Secrets:** one file each, readable by `lux-webui` only:
   ```sh
   cd /etc/lux-webui
   for f in session oidc-client ldap-bind db store; do install -m 0640 -g lux-webui /dev/null $f.secret; done
   openssl rand -hex 32 > session.secret        # then fill in the others with your editor
   ```
4. **Configure:** edit `/etc/lux-webui/config.yaml`. Every setting is commented.
5. **First collector run:** `systemctl start lux-collector.service`, then `journalctl -u lux-collector`. It creates the `lux_webui` tables on first run.
6. **Start the web service and the timer:** `systemctl enable --now lux-webui.service lux-collector.timer`
7. **nginx:** copy `/usr/share/lux-webui/nginx-lux-webui.conf.example` to `/etc/nginx/conf.d/lux-webui.conf`, set `server_name` and the certificate paths, then run:
   ```sh
   setsebool -P httpd_can_network_connect 1      # SELinux: nginx may proxy to the app
   nginx -t && systemctl enable --now nginx
   firewall-cmd --permanent --add-service=https && firewall-cmd --reload
   ```
8. **Check it:** sign in as a member of a bootstrap admin group, then open **Administration › Directory** and run the connection test.

## Notes

- **One worker.** Login sessions live in memory, so the service runs a single process, and a restart signs everyone out. A shared session store will lift this.
- **Certificates.** Python verifies TLS strictly. LDAP and Keycloak certificates must chain to a trusted CA: the system store, or `ca_file` in the config. Checkmk 2.5's agent additionally requires an Authority Key Identifier in each certificate.
- **Logs.** Everything goes to journald: `journalctl -u lux-webui`. Audit events are JSON lines on the `lux_webui.audit` logger. They are also copied to the database for the Audit log page.
- **Upgrades.** `dnf upgrade ./lamplighter-webui-*.rpm` keeps your `config.yaml` and restarts the service. Database migrations run on start.
