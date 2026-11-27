# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Lamplighter, a fork of the Robinhood Policy Engine (v3) by CEA: a C daemon plus CLI tools that mirror filesystem metadata into a database (MySQL/MariaDB by default, SQLite optional) and run attribute-based policies (purge, migration/archive, rmdir, alerts, checksums...) over it. It works on any POSIX filesystem and has extra features for Lustre (MDT changelogs, OSTs/pools, HSM). It targets Linux (RHEL-like): it does not build natively on macOS, so build and test on a Linux host or VM.

## Fork goals

This fork keeps the Robinhood v3 engine and its MariaDB backend, and modernizes everything around them:

- **Leave the C engine alone.** The scanner, changelog reader, pipeline, policy engine and modules in `src/` are trusted as they are. Make changes outside `src/` where possible. If a C change seems unavoidable, raise it before making it.
  - **Later: an incremental move to Rust.** Once the web UI features are finished, the C core may be replaced by Rust step by step, from the outside in (read-only tools first, deleting policy actions last), each step proven identical to the C behavior by differential tests, on feature branches. That work is planned in its own discussion first: until then, this rule stands.
- **Replace the PHP web GUI** (Robinhood's `gui_v3`: PHP + Bootstrap 3/jQuery/DataTables; deleted from this repository, last copy at tag `robinhood-php-gui-final`). It must not use PHP in any form, because PHP must not need to be installed on the servers. The new GUI reads the existing v3 MariaDB schema directly (the old GUI does the same) instead of requiring engine changes.
  - **UX:** modern and information-dense with drill-down, modelled on the *visual appearance* of the Checkmk Pro GUI. Only the look is borrowed: no Checkmk/Nagios monitoring concepts such as OK/WARN/CRIT states or service checks.
  - **Auth and ACLs (core goal):** multi-user from the start. Login is OpenID Connect against **Keycloak** (preferred) or an optional direct LDAP bind. User identity and groups always come from **LDAP**, with TLS required, configurable base DNs and filters, and RFC 2307 or RFC 2307bis support. ACLs map permissions to roles to LDAP groups, and are enforced in the API. Checkmk authenticates to the API with a Keycloak client-credentials token. See `doc/webui-design.md` section 4.
  - **Layout:**
    - Never use HTML frames (`<frame>`, `<frameset>`, `<iframe>`). Every view is a whole page with its own URL, and there is one scroll per page.
    - Panels go on a column grid with one gutter and content-sized heights, and never trap whitespace.
    - See `doc/webui-design.md` §5.0.1.
  - **Colors:** see `webui/design/palette-draft.html`. The UI is dark-first (Ubuntu charcoal plus Carolina Blue); the light theme has no bright white backgrounds, and the sidebar has a translucent aubergine wash in both themes (faint over charcoal in dark mode). Aubergine appears only on the login screen, code panels, the top-bar line and the sidebar.
  - **No copied Checkmk code:** Checkmk's LDAP connector is proprietary (Pro edition). Use it as a functional reference only.
- **Operational views** the GUI should provide:
  - **Slowdown triage:** what data is hot (recent access or modification) by user, group and directory. Robinhood only sees metadata, and Lustre updates atime lazily and leaves ATIME changelog records off by default. Real per-job I/O comes from Lustre jobstats, shown alongside Robinhood data with job IDs as Lustre labels them (`jobid_name`: process name and UID by default, or a scheduler's job ID). **No job scheduler dependency** (not Slurm, nor any other); scheduler details may become an optional plug-in later. Jobstats come from a spool directory by default (a cron one-liner on each Lustre server drops snapshots), with Prometheus as the only alternative source.
  - **Cold data for data managers:** the oldest, largest and least-used files and directories, with their owners to contact, as candidates to purge or move to cold storage (for example AWS Glacier).
- **Checkmk Pro is the only alerting tool.** Robinhood and the web UI never alert or notify: no alert mail, no syslog alerts, no alert states in the UI. They expose read-only metrics (Robinhood status, usage, per-policy/fileclass match counts) through a monitoring endpoint that **Checkmk polls**: a special agent on the Checkmk server, in an MKP targeting Checkmk Pro 2.5, with nothing installed on the Robinhood host. The Checkmk plugin holds all thresholds. Robinhood alert policies are used only as classifiers. See `doc/webui-design.md` section 7.
- **Modernize outdated tooling**, preferring Python to Perl. `scripts/type_gen.pl` generates `db_schema.h` at build time, so any port must produce identical output. `scripts/checkpatch.pl` is the Linux kernel's style checker; replacing it with `clang-format` is preferable to rewriting it.

## Build

Autotools project:

```sh
sh autogen.sh                 # after a git checkout: generates configure
./configure [options]         # e.g. --disable-lustre for a POSIX-only build
make                          # builds everything under src/
make rpm                      # binary RPMs into rpms/RPMS/<arch>
./configure --enable-dist && make srpm   # src rpm with minimal deps
```

Build deps: glib2-devel, libattr-devel, mysql/mariadb-devel, bison, flex, jemalloc-devel (plus lustre-devel >= 2.15 for Lustre support). Useful configure switches (see `configure.ac`): `--disable-lustre`, `--disable-changelogs`, `--disable-shook`, `--disable-jemalloc`, `--with-lustre=<dir>`. The `PURPOSE_CFLAGS`/`DB_CFLAGS` substitutions select compile-time features (Lustre, FID support, DB backend), which show up throughout the code as `#ifdef _LUSTRE`, `_HAVE_FID`, `_MYSQL`/`_SQLITE`, etc.

Other top-level targets: `make cppcheck`, `make lcov`, `make check-valgrind` (unit tests under valgrind), `make -C src indent`.

## Tests

- **Unit tests** in `src/tests/` (automake `check_PROGRAMS`: `test_uidgidcache`, `test_params`, ...): `make check` or `make -C src/tests check`.
- **Integration test suite** in `tests/test_suite/` (see its `README.rst`). It must run as root on a real filesystem and DB, from that directory, using the binaries in the source tree:
  ```sh
  cd tests/test_suite
  ./1-test_setup_posix.sh            # or ./1-test_setup_lustre.sh (Lustre mounted at /mnt/lustre, >= 4 OSTs)
  POSIX_MODE=1 NOLOG=1 ./2-run-tests.sh -q      # whole suite, brief output
  ./2-run-tests.sh 3,4                # only selected tests (ids like 101a, or 101 for all 101x)
  DEBUG=1 ./2-run-tests.sh 100        # debug output
  WITH_VALGRIND=1 ./2-run-tests.sh 301
  ```
  `PURPOSE` selects the test profile: `TMP_FS_MGR` (default), `LUSTRE_HSM`, `BACKUP`, `SHOOK`. Tests are registered at the bottom of `2-run-tests.sh` as `run_test <id> <function> <cfg> ... "<title>"`, and their configs live in `tests/test_suite/cfg/`.

## Web UI development environment

`webui/devenv/` is a Podman/Docker Compose environment (see its README). It runs Rocky 9.7, MariaDB 10.5, OpenLDAP (RFC 2307bis, TLS only) and Keycloak 26 (realm federated from the LDAP server), all with certificates from a throwaway test CA. It works on macOS, so use it to build and run Robinhood, and to test anything involving LDAP, Keycloak or the database:

```sh
webui/devenv/devenv.sh all     # from scratch: certs, start, build, sample data, scan, all tests
webui/devenv/devenv.sh build   # rebuild Robinhood (incremental)
webui/devenv/devenv.sh scan    # rescan the sample tree into MariaDB
webui/devenv/devenv.sh collect # run the web UI collector once
webui/devenv/devenv.sh test    # smoke test; must stay green
webui/devenv/devenv.sh webui-test [-k EXPR]  # back-end pytest suite (unit + live LDAP/Keycloak/DB); must stay green
webui/devenv/devenv.sh shell   # shell in the Rocky 9.7 workbench (repo mounted read-only at /src)
```

On macOS, `docker` is Podman (`podman machine start` if it isn't running). Test identities come from `webui/devenv/ldap/data.ldif`. Never point the dev environment at real LDAP or Keycloak servers.

Web UI back end: `webui/backend/` (Python 3.12, FastAPI, package `lux_webui`). Configuration is `config.py` (YAML; secrets always in separate `*_file`s), LDAP is `ldapdir.py`, ACLs are `acl.py` (permissions, roles, LDAP group DNs), login and sessions are `auth.py` and `sessions.py`, Checkmk bearer-token checks are `machine.py`, admin APIs are `admin_api.py` (+ `ldapdiag.py` for LDAP diagnostics; responses are built field by field so secrets can never leak), usage APIs are `usage_api.py`, and DB reads are `db.py` (cheap reads only: never scan `ENTRIES` on a request). The collector (`collector.py`, CLI `lux-collector`) is the only code that scans `ENTRIES`: once per run, aggregated by MariaDB (`GROUP BY`), writing to the UI's own `lux_webui` schema (`rollup.py` adds per-directory subtree totals via a recursive CTE into a TEMPORARY table) (`store.py`; schema changes are new numbered files in `migrations/`, never edits to released ones). The dev config is `webui/devenv/webui/config.yaml`.

Web UI front end: `webui/frontend/` (React + TypeScript + Vite, ECharts; no component library). Node runs only in the devenv `frontend` container, which rebuilds `dist/` on every save; the back end serves `dist/` (any non-API URL gets `index.html` after a login check). Colors come only from the tokens in `src/styles/tokens.css`; layout follows the rules in `src/styles/app.css`. Pages and their permissions are listed in `src/nav.tsx`. Before adding an npm dependency, check its license (all must be GPLv3-compatible). `webui/devenv/devenv.sh shots` renders pages in headless Chromium (both themes, phone width) to `webui/devenv/.shots/`, and reports console errors and horizontal overflow.

Checkmk plugin: `webui/checkmk/` (Checkmk 2.5 plug-in family `cmk_addons/plugins/lamplighter`: special agent, agent-based checks, rulesets, server-side calls; see its README). Test with `webui/devenv/devenv.sh checkmk-test` (Python 3.13 + Checkmk's real APIs copied out of the image into `webui/devenv/.cmk-api/`, never committed) and build the MKP with `devenv.sh mkp`. Checkmk images are x86-64 only and a site does not run under emulation on Apple Silicon. Secrets go to the agent by password-store reference only (`--client-secret-id`).

Packaging: `webui/packaging/` builds the `lamplighter-webui` RPM (`build-rpm.sh`: front end in a Node container, rpmbuild in a throwaway Rocky 9.7 container; the venv is built at its real path `/opt/lux-webui`) and `test-rpm.sh` installs it in a fresh Rocky 9.7 container behind nginx against the dev environment. The RPM's architecture is the build host's: x86_64 production packages need an x86_64 build host. The PHP GUI has been removed entirely (last copy at tag `robinhood-php-gui-final`). Rocky 9 ships nginx 1.20 (no `http2 on;`).

## Architecture

`src/` is split into static libtool libraries that are linked into every executable in `src/robinhood/` (the `lamplighter` daemon, `lux-report`, `lux-find`, `lux-du`, `lux-diff`, `lux-undelete`, `lux-rebind`; sources keep Robinhood's names, e.g. `rbh_find.c`):

- `include/`: shared headers. `db_schema.def` is processed by `scripts/type_gen.pl` into `db_schema.h` (the entry attribute set and DB fields), so edit the `.def` file and never the generated header.
- `cfg_parsing/`: bison/flex config grammar (`conf_yacc.y`, `conf_lex.l`), boolean expressions for policy rules, and config helpers. Each module registers its config block through `rbh_cfg`.
- `list_mgr/`: the DB abstraction layer ("ListMgr"): inserts, updates, gets, filters, iterators, reports, namespace, tags, recovery. `mysql_wrapper.c` and `sqlite_wrapper.c` are the backend-specific parts.
- `fs_scan/`: multi-threaded namespace scanner that feeds entries into the entry processor.
- `chglog_reader/`: Lustre MDT changelog consumer (only built with changelogs), which also feeds the entry processor.
- `entry_processor/`: a staged, multi-threaded pipeline every scanned or changelog entry goes through (`std_pipeline.c`: GET_FID → GET_INFO_DB → GET_INFO_FS → PRE_APPLY → DB_APPLY → CHGLOG_CLR → RM_OLD_ENTRIES). `diff_pipeline.c` is the variant used by `rbh-diff`. Entries with the same ID are serialized through `entry_proc_hash`.
- `policies/`: policy definitions and loading, rule/fileclass matching, triggers (usage thresholds, periodic), schedulers, and the policy run loop. `status_manager.c` handles per-policy entry statuses.
- `modules/`: dlopen'ed plugins (`librbh_mod_<name>.so`) that implement the `rbh_module_operations` interface (`include/rbh_modules.h`) and supply status managers, actions, and schedulers, referenced in config as `<module>.<action>` (e.g. `lhsm.archive`, `common.unlink`). Includes `lhsm`, `backup`, `shook`, `alerter`, `checker`, `modeguard`, `basic`, and `common`.
- `common/`: logging (`rbh_logs`), global config, uid/gid cache, queues and locks, Lustre helpers, and module loading.
- `tools/`: Lustre maintenance and recovery utilities.

Policies themselves are mostly declared in config: `doc/templates/includes/*.inc` define the standard policies (tmpfs, lhsm, backup, rmdir...), which the user's config includes. When run from the source tree, the test suite points `LD_LIBRARY_PATH` at `src/modules/.libs`.

Other top-level dirs: `scripts/` (`lux-config` DB/changelog setup and `check` helper, `lux_cksum.sh`, systemd units, code-style scripts), `man/`, `doc/`.

## Conventions

- **Naming and compatibility** (the contract is `doc/COMPATIBILITY.md`; never break it):
  - User-visible names are Lamplighter's: the `lamplighter` daemon, `lux-*` commands, `lux-config`, `lux_cksum.sh`, `luxext_tool*`, `lamplighter@<fs>.service`, `/etc/lamplighter.d`, `LUX_CFG_DEFAULT`, `lamplighter-*` RPMs (old names only in `lamplighter-compat`).
  - Internal identifiers keep Robinhood's names (`src/robinhood/`, `rbh_*.c`, `rbh_*` functions, `RBH_*` macros, `librbh_mod_*`), to keep merging CEA's fixes easy.
  - Never change Robinhood's database schema, the configuration syntax, module names, database names or the default database user (`robinhood`).
  - The list of renamed helpers exists twice and must stay in sync: `renamed_cmds[]` in `src/policies/policy_loader.c` and `RENAMED_CMDS` in `scripts/lux-config`.
- **Licensing:**
  - New code under `webui/` is **GPL-2.0-or-later**: each file carries `SPDX-License-Identifier: GPL-2.0-or-later`, and the license text is in `webui/LICENSE`.
  - Changes to Robinhood's own files stay **CeCILL-C**.
  - Dependencies must be GPLv3-compatible (MIT/BSD/Apache-2.0/PSF/MPL-2.0/LGPL/GPL): check before adding one.
  - Never copy code from proprietary sources.
  - See `doc/webui-design.md` section 10.

- C style: 4-space indent, no tabs, 80 columns, kernel-like brace style. `scripts/indent.sh <file>` runs GNU indent plus `checkpatch.pl` with the project's ignore list.
- Commit messages follow `.commit-template`: `<module>: short description`, then a blank line and a detailed explanation (for example `listmgr: ...`, `tests: ...`, `rbh_daemon: ...`).