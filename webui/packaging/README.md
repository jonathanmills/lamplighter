# Installing the Lamplighter web UI (Rocky Linux / RHEL 9)

The `lamplighter-webui` RPM installs:

| Path | What |
|---|---|
| `/opt/lamplighter-webui/` | The back end in its own Python 3.12 virtual environment. No system Python packages are touched. |
| `/usr/share/lamplighter-webui/frontend/` | The built front end (static files, served by the back end) |
| `/etc/lamplighter.d/webui/config.yaml` | Configuration (`root:lamplighter-webui`, mode 0640); a commented example on first install |
| `lamplighter-webui.service` | The web service: 127.0.0.1:8000, as user `lamplighter-webui`, sandboxed by systemd |
| `lamplighter-collector.service` / `.timer` | The collector, nightly at 02:30 (±30 min) |
| `lamplighter-jobstats.service` / `.timer` | Reads Lustre jobstats for the triage view, every minute (when configured) |
| `/var/spool/lamplighter-jobstats/` | Where Lustre servers deliver jobstats snapshots (`2770 lamplighter-webui:lamplighter-jobstats`) |
| `/usr/share/lamplighter-webui/*.example` | Templates: `nginx-lamplighter-webui.conf.example` and a pristine `config.yaml.example` |
| `/usr/share/doc/lamplighter-webui/` | This file |

No PHP, no Node.js and no compiler is needed on the server. The PHP GUI (`robinhood-webgui`) is no longer built.

## Building the RPM

On any Rocky Linux / RHEL 9 build host (x86-64 or ARM), from a git clone:

```sh
webui/packaging/make-srpm.sh           # or "make webui-srpm"; needs only rpm-build and tar
sudo dnf module enable -y nodejs:22    # Node.js 18+ for the front end, on the build host only
sudo dnf builddep webui/packaging/dist/lamplighter-webui-*.src.rpm
rpmbuild --rebuild webui/packaging/dist/lamplighter-webui-*.src.rpm
```

The build downloads Python and npm packages (internet access or local mirrors). It creates the virtual environment at its real path, `/opt/lamplighter-webui`, so use a build host where that path is not in use. Maintainers can run the same steps in a throwaway container with `build-rpm.sh`.

## Steps

1. **Install:** `dnf install ./lamplighter-webui-<version>.el9.<arch>.rpm nginx`
2. **Databases:** let your DBA create:
   - on the Robinhood database (preferably a replica): user `lamplighter_webui` with `SELECT` and `CREATE TEMPORARY TABLES` on the Robinhood schema
   - a schema `lamplighter_webui` with `ALL` for that user (it can live on another server)
3. **Secrets:** one file each, readable by `lamplighter-webui` only:
   ```sh
   cd /etc/lamplighter.d/webui
   for f in session oidc-client ldap-bind db store; do install -m 0640 -g lamplighter-webui /dev/null $f.secret; done
   openssl rand -hex 32 > session.secret        # then fill in the others with your editor
   ```
4. **Configure:** edit `/etc/lamplighter.d/webui/config.yaml`. Every setting is commented.
5. **First collector run:** `systemctl start lamplighter-collector.service`, then `journalctl -u lamplighter-collector`. It creates the `lamplighter_webui` tables on first run.
6. **Start the web service and the timer:** `systemctl enable --now lamplighter-webui.service lamplighter-collector.timer`
7. **nginx:** copy `/usr/share/lamplighter-webui/nginx-lamplighter-webui.conf.example` to `/etc/nginx/conf.d/lamplighter-webui.conf`, set `server_name` and the certificate paths, then run:
   ```sh
   setsebool -P httpd_can_network_connect 1      # SELinux: nginx may proxy to the app
   nginx -t && systemctl enable --now nginx
   firewall-cmd --permanent --add-service=https && firewall-cmd --reload
   ```
8. **Check it:** sign in as a member of a bootstrap admin group, then open **Administration › Directory** and run the connection test.

## Notes

- **One worker.** Login sessions live in memory, so the service runs a single process, and a restart signs everyone out. A shared session store will lift this.
- **Certificates.** Python verifies TLS strictly. LDAP and Keycloak certificates must chain to a trusted CA: the system store, or `ca_file` in the config. Checkmk 2.5's agent additionally requires an Authority Key Identifier in each certificate.
- **Logs.** Everything goes to journald: `journalctl -u lamplighter-webui`. Audit events are JSON lines on the `lamplighter_webui.audit` logger. They are also copied to the database for the Audit log page.
- **Upgrades.** `dnf upgrade ./lamplighter-webui-*.rpm` keeps your `config.yaml` and restarts the service. Database migrations run on start.
