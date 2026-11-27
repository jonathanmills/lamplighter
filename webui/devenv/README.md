# Lamplighter web UI: development environment

A self-contained test environment that runs on a laptop with Podman or Docker. Everything uses real TLS, signed by a throwaway test CA, and **no external systems are contacted**.

| Service | What it is | Reachable as |
|---|---|---|
| `ldap` | OpenLDAP with an RFC 2307bis schema (`groupOfNames` groups, users with `uid` and `memberOf`). It serves LDAPS and StartTLS only: plain-text LDAP is refused. | `ldaps://ldap.test:636` (host: `127.0.0.1:6636`) |
| `keycloak` | Keycloak 26.0.8 with the `lamplighter` realm. Users are federated read-only from the LDAP server over LDAPS. | `https://keycloak.test:9443` (host: `127.0.0.1:9443`) |
| `mariadb` | MariaDB 10.5 holding the Robinhood database `robinhood_dev` | `mariadb:3306` (host: `127.0.0.1:13306`) |
| `workbench` | Rocky Linux 9.7: builds and runs Robinhood (POSIX mode), and runs the tests | `./devenv.sh shell` |
| `webui` | The web UI back end (FastAPI on Rocky 9.7, Python 3.12). It reloads live when you edit `webui/backend/src`, and serves the built front end. | `https://webui.test:8443` (host: `127.0.0.1:8443`) |
| `frontend` | Node 22 build container: rebuilds `webui/frontend/dist` within about 3 seconds of a front-end file changing (refresh the browser to see the change). It polls for changes, because file events from a macOS host never reach containers in the Linux VM; the back end polls for the same reason. | none |

### Optional services (opt-in, not started by `devenv.sh all`)

| Service | What it is | Start with | Address |
|---|---|---|---|
| `prometheus` + `jobstats-exporter` | Prometheus 2.55 scraping a simulated Lustre jobstats exporter (`tools/fake_jobstats_exporter.py`), whose counters grow in real time. Used to try the Prometheus jobstats source; `tests/integration/test_prometheus_live.py` runs only while it is up. | `./devenv.sh prometheus` (stop: `prometheus-down`) | `http://127.0.0.1:9090` |
| `checkmk` | Checkmk Community 2.5 for installing the plugin in a real site. **x86-64 hosts only**: Checkmk publishes no ARM image, and a site does not run under emulation on Apple Silicon. On a Mac, `./devenv.sh checkmk-test` tests the plugin against Checkmk's real Python APIs instead; the end-to-end test belongs on a real Checkmk server. | `docker compose --profile checkmk up -d checkmk` | `http://127.0.0.1:5000/cmk/` |

### Scale tests

`lamplighter-scale` (part of the web UI package) builds a synthetic Robinhood database of any size and times the collector's passes and the request-time queries against it. In the dev environment (MariaDB has a 4 GB buffer pool for this):

```sh
R=mysql+pymysql://root:devroot@mariadb
docker compose -p lamplighter-dev exec webui python -m lamplighter_webui.scale gen \
    --url $R/ --template robinhood_dev --target robinhood_scale --entries 10000000
docker compose -p lamplighter-dev exec webui python -m lamplighter_webui.scale bench --url $R --db robinhood_scale
```

On site hardware, run the installed `/opt/lamplighter-webui/bin/lamplighter-scale` the same way, against a scratch MariaDB server (never the production Robinhood server), with `--template` naming a copy of the site's Robinhood database so the schema matches.

## Quick start

```sh
cd webui/devenv
./devenv.sh all      # certs, images, start, build Robinhood, sample data, scan, all tests
```

The first run downloads images and takes a few minutes. Later runs take seconds. Run `./devenv.sh` with no arguments to list all commands. The main ones:

- `./devenv.sh scan` re-scans the sample tree into MariaDB.
- `./devenv.sh collect` runs the web UI collector once: the age histogram and usage snapshot, written to the `lamplighter_webui` schema. The Overview's age chart and the Cold data page need at least one run.
- `./devenv.sh data --scale 4` generates four times as many files (about 100k).
- `./devenv.sh sql` opens a SQL shell on the Robinhood database.
- `./devenv.sh test` runs the smoke test (19 checks covering LDAP, Keycloak and Robinhood/MariaDB).
- `./devenv.sh webui-test` runs the back end's test suite: unit tests, plus integration tests that drive real logins through Keycloak and LDAP. Add pytest options after it, for example `./devenv.sh webui-test -k monitoring`.
- `./devenv.sh shots` renders pages in headless Chromium (both themes, phone width) to `.shots/`, and reports console errors and layouts wider than the screen.
- `./devenv.sh down` stops everything but keeps the data. `./devenv.sh reset` wipes everything.

## Test identities (all passwords are test-only)

| User | Unix group | Web UI role group | Story in the sample data |
|---|---|---|---|
| `alice` | proj-astro | lamplighter-viewers | Active analysis: mostly hot data |
| `bob` | proj-astro, proj-climate | — | Small-file storm in `/data/scratch` in the last 2 hours |
| `carol` | hpc-staff | lamplighter-data-managers | Staff |
| `dave` | hpc-staff | lamplighter-admins | Staff |
| `erin` | proj-climate | lamplighter-viewers | Large climate output, 1 month to 1 year old |
| `frank` | proj-genomics | — | Cold genomics data, untouched for 2 to 6 years |

- Every user's password is `password`.
- The LDAP service bind for the web UI is `cn=lamplighter-webui,ou=services,dc=lamplighter,dc=test` with password `webui-bind-secret`.
- Keycloak clients:
  - `lamplighter-webui` (secret `dev-webui-client-secret`): authorization code with PKCE. Direct password grants are also enabled, for automated tests only.
  - `lamplighter-checkmk` (secret `dev-checkmk-client-secret`): client credentials, with the `monitoring` role.
- The Keycloak admin console login is `admin` / `admin`.

`ldap/data.ldif` is the single source of truth: the workbench's Unix accounts and the owners of the generated files are derived from it.

## Notes

- Sample files are **sparse**: their sizes (terabytes in total) cost no disk space, so `blocks` is near zero in Robinhood's data.
- The scanned tree lives on a tmpfs inside the workbench and is regenerated by `./devenv.sh data`. The database persists in a volume.
- To use the UI in a browser on the Mac, add `127.0.0.1 keycloak.test webui.test` to `/etc/hosts`, and either trust `.certs/ca.pem` in Keychain Access or accept the certificate warnings. Then open `https://webui.test:8443`. The automated tests don't need this.
- Robinhood is built from a copy of the repository inside a volume, so no build artifacts appear in the source checkout.
- `devenv.sh` uses a private, empty Docker client config (`.docker-config/`), so settings in `~/.docker/config.json` can't interfere.
