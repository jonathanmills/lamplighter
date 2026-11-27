# Lamplighter's compatibility promise with Robinhood

*Status: agreed design, November 2026. The rename has not happened yet, so the code still uses the Robinhood names. This document is the contract the rename and all later work must keep.*

Lamplighter is a fork of the [Robinhood Policy Engine](https://github.com/cea-hpc/robinhood) by CEA. As a firm project goal, out of respect for Robinhood and to make trying Lamplighter safe, **a site running Robinhood can move to Lamplighter, or back, without converting anything.**

## 1. The database: unchanged

- **No changes to Robinhood's database schema or its data structures.** Lamplighter uses Robinhood 3.2's tables, columns, triggers, stored functions and `VARS` entries exactly as they are.
- Robinhood adapts its schema to the configuration: enabling a policy, for example, adds that policy's status columns. Lamplighter keeps that behavior exactly, so **the same configuration produces the same schema**, in either program.
- The web UI keeps its own data (collector results and the audit copy) in a **separate schema** (`lux_webui`). It never writes Robinhood's tables, and Robinhood is unaffected by its presence.
- Database names belong to the site (for example `robinhood_lustre`) and are never renamed.

## 2. Configuration files: same contents, Lamplighter's directory

- Lamplighter reads its configuration only from **`/etc/lamplighter.d/`**. It does **not** read `/etc/robinhood.d/`.
- A Robinhood configuration file, for example `/etc/robinhood.d/my-lustre-fs.conf`, **works unchanged when copied** to `/etc/lamplighter.d/my-lustre-fs.conf`:
  - The configuration syntax, block names, parameters, module names (`lhsm.archive`, `common.unlink`, …) and policy definitions are identical.
  - Lamplighter ships the standard include files under the **same names** in `/etc/lamplighter.d/includes/`, so lines like `%include "includes/tmpfs.inc"` resolve. A site's *own* include files are copied along with the configuration.
  - Absolute paths inside a file, such as `password_file` and `log_file`, work as written, as long as those files exist. Updating them is optional.

### Commands named in configuration files

Policy actions can run external programs, for example `cmd("/usr/sbin/rbh_cksum.sh '{output}' '{path}'")`. Some of those programs are helpers that Robinhood ships and Lamplighter renames. Lamplighter handles them as follows:

1. **Only an exact list of names Lamplighter itself renamed** is recognised: the `rbh-*` commands, `rbh_cksum.sh`, `rbhext_tool` and similar shipped helpers. There is no pattern matching.
2. **Only the program at the start of a `cmd(...)` command line** is considered. Nothing else is ever rewritten: not other text in the command, not file paths, database names or `$VARIABLES`, and nothing outside `cmd(...)`.
3. **If the old name is not installed,** the program name is translated to the new one, and a **warning** is logged whenever the configuration is loaded, quoting the parameter and the command, the old and new names, and how to update the file.
4. **If `lamplighter-compat` is installed** (which provides the old names), nothing is translated. The warning is still logged, so admins see exactly what runs.
5. **`lux-config check FILE`** reports every occurrence with its file and line (including files the configuration `%include`s), and `lux-config check --fix FILE` writes `FILE.lamplighter` copies to review. It never edits a configuration in place.

These strings are commands that run on production data, sometimes deleting it, so the rules are deliberately narrow and always visible.

## 3. Commands, daemon and packages

| Robinhood | Lamplighter |
|---|---|
| `rbh-find`, `rbh-du`, `rbh-report`, `rbh-diff`, `rbh-undelete`, `rbh-rebind` | `lux-find`, `lux-du`, `lux-report`, `lux-diff`, `lux-undelete`, `lux-rebind` |
| `rbh-config` | `lux-config` |
| daemon `robinhood` | daemon `lamplighter` |
| `robinhood@<fs>.service`, `/etc/sysconfig/robinhood.<fs>` | `lamplighter@<fs>.service`, `/etc/sysconfig/lamplighter.<fs>` |
| `/etc/robinhood.d/` | `/etc/lamplighter.d/` |
| RPMs `robinhood`, `robinhood-adm`, `robinhood-lustre`/`-posix`, … | `lamplighter`, `lamplighter-adm`, `lamplighter-lustre`/`-posix`, …, with `Obsoletes`/`Provides` so an upgrade replaces Robinhood cleanly |

- **`lamplighter-compat`** is an optional package for sites with existing scripts and cron jobs. It provides the old names as links: `rbh-*` → `lux-*`, `robinhood` → `lamplighter`, and the renamed helpers.
- **Internal source identifiers stay** (`rbh_*` functions, `RBH_*` macros, module library names). Users never see them, and keeping them makes merging CEA's future fixes easy.

## 4. Trying Lamplighter on a copy of a Robinhood database

1. `mysqldump` the Robinhood database, and restore it on a test node. The node must be a Lustre client configured like the production one, with the same filesystem mounted.
2. Install Lamplighter, copy the configuration file into `/etc/lamplighter.d/`, and point it at the restored database.
3. Use the read-only tools and the web UI: `lux-report`, `lux-find`, `lux-du`.

**Warning: the test node shares the production filesystem.**
- **Never read the MDT changelog with the production reader ID** (usually `cl1`). Reading clears records, which takes them away from the production Robinhood. Run scan-free and read-only, or register a separate changelog reader for the test.
- **Never run real policies.** A purge on the test node deletes *real files* on the shared filesystem. Use dry-run modes only.

Going back is equally simple: the database and configuration files were never converted.
