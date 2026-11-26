# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Robinhood Policy Engine (v3): a C daemon plus CLI tools that mirror filesystem metadata into a database (MySQL/MariaDB by default, SQLite optional) and run attribute-based policies (purge, migration/archive, rmdir, alerts, checksums...) over it. It works on any POSIX filesystem and has extra features for Lustre (MDT changelogs, OSTs/pools, HSM). It targets Linux (RHEL-like): it does not build natively on macOS, so build and test on a Linux host or VM.

## Fork goals

This fork keeps the Robinhood v3 engine and its MariaDB backend, and modernizes everything around them:

- **Leave the C engine alone.** The scanner, changelog reader, pipeline, policy engine and modules in `src/` are trusted as they are. Make changes outside `src/` where possible. If a C change seems unavoidable, raise it before making it.
- **Replace the PHP web GUI** (`web_gui/gui_v3`: PHP + Bootstrap 3/jQuery/DataTables). It must not use PHP in any form, because PHP must not need to be installed on the servers. The new GUI reads the existing v3 MariaDB schema directly (the old GUI does the same) instead of requiring engine changes.
  - **UX:** a modern, information-dense ops-console style with drill-down, modelled on the Checkmk Pro interface.
  - **Auth:** secure multi-user federation via OpenID Connect from the start, with roles in mind (for example HPC admins and data managers).
- **Operational views** the GUI should provide:
  - **Slowdown triage:** what data is hot (recent access or modification) by user, group and directory, correlated with Slurm jobs. Robinhood only sees metadata, and Lustre updates atime lazily and leaves ATIME changelog records off by default. Real per-job I/O should come from Lustre jobstats (`jobid_var=SLURM_JOB_ID`) shown alongside Robinhood data.
  - **Cold data for data managers:** the oldest, largest and least-used files and directories, with their owners to contact, as candidates to purge or move to cold storage (for example AWS Glacier).
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
- `web_gui/tests/nonreg.bash` covers the PHP web GUI (`scripts/pre-commit` runs it when `web_gui/` changes).

## Architecture

`src/` is split into static libtool libraries that are linked into every executable in `src/robinhood/` (`robinhood` daemon, `rbh-report`, `rbh-find`, `rbh-du`, `rbh-diff`, `rbh-undelete`, `rbh-rebind`):

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

Other top-level dirs: `scripts/` (`rbh-config` DB/changelog setup helper, systemd units, code-style scripts), `web_gui/` (PHP GUI), `man/`, `doc/`.

## Conventions

- C style: 4-space indent, no tabs, 80 columns, kernel-like brace style. `scripts/indent.sh <file>` runs GNU indent plus `checkpatch.pl` with the project's ignore list.
- Commit messages follow `.commit-template`: `<module>: short description`, then a blank line and a detailed explanation (for example `listmgr: ...`, `tests: ...`, `rbh_daemon: ...`).