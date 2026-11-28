# Lamplighter release notes

## 1.0.0

The first release of Lamplighter, a fork of the **Robinhood Policy
Engine 3.2.0** by CEA. Robinhood's scanner, changelog reader, pipeline
and policy engine are kept as they are; Lamplighter's work is around
them: a new web UI, monitoring through Checkmk, modern packaging, and a
consistent name. Our thanks go to CEA and every Robinhood contributor.

### Compatibility with Robinhood

- **The same database schema.** An existing Robinhood 3 database works
  unchanged, and so does a `mysqldump` of one.
- **The same configuration format.** A Robinhood configuration file
  works when copied to `/etc/lamplighter.d/`. Command names in `cmd()`
  actions that Lamplighter renamed are reported by
  `lamplighter-config check`.
- **Upgrades replace Robinhood 3 packages** by name (`robinhood-lustre`,
  `robinhood-posix`, `robinhood-adm`).
- **Robinhood's command names**, for existing scripts, come from the
  optional `lamplighter-compat` package.
- One Lamplighter installation per filesystem, as with Robinhood.

See `doc/COMPATIBILITY.md` and `doc/NAMING.md`.

### Names

- Tools for any user, in `/usr/bin`: `lux-find`, `lux-du`.
- Everything else is `lamplighter`: the daemon and its systemd unit,
  the administration tools in `/usr/sbin`, `/etc/lamplighter.d`, and
  the packages.

### The web UI (`lamplighter-webui`)

It replaces Robinhood's PHP GUI (`gui_v3`), and is feature-complete
with it. It is written in Python (FastAPI) and React, and needs no PHP.

**Pages:**
- **Overview:** Robinhood's status (changelog, last scan), usage,
  collector runs.
- **Users & groups:** usage per owner and group from Robinhood's
  accounting, each owner's page with history and data age, and the
  largest users or groups stacked over time.
- **Triage:** "the filesystem is slow, who is doing what?" Lustre
  jobstats (busiest jobs, Lustre targets, metadata and I/O over 15
  minutes to 24 hours) next to what Robinhood knows. Jobstats come from
  a spool directory, or from Prometheus. No job scheduler is needed;
  scheduler details are an optional plug-in hook, off by default.
- **Cold data:** the oldest, largest and least-used data, the coldest
  directories, and their owners to contact, for purging or moving to
  cold storage.
- **Fileclasses & policies:** entries and volume per Robinhood
  fileclass and policy status.
- **Search:** files by owner, group, name, size and age, within a
  directory tree. With the optional replica indexes, the whole
  filesystem can be searched too.
- **Browse:** directory trees with subtree totals. People with the
  `user` or `viewer` role browse their own files only.
- **Reports:** any table on a schedule (hourly, daily, weekly), as a
  history with custom and stacked graphs, and optional email to
  permitted recipients through the site's mail relay.
- **Exports:** every table downloads as CSV or JSON.
- **Administration:** directory (LDAP) settings and tests, access
  control, and the audit log.

**Access:**
- Sign-in with OpenID Connect (Keycloak) or a direct LDAP bind.
- Identity and groups come from LDAP (RFC 2307 or 2307bis, TLS
  required).
- Roles (`user`, `viewer`, `data-manager`, `admin`) map to LDAP groups,
  and are enforced in the API.
- Every sign-in, denial, view of personal data, export and report is
  audited (journald, plus a copy in the database).

**Background jobs** (systemd timers):
- `lamplighter-collector` pre-computes data ages, directory totals and
  policy counts from Robinhood's database (ideally a replica).
- `lamplighter-jobstats` reads jobstats every minute.
- `lamplighter-reports` runs the scheduled reports.

### Monitoring with Checkmk

Checkmk Pro is the tool for fault detection and alerting. A special
agent and rulesets for Checkmk 2.5 (an MKP, `lamplighter-1.0.0.mkp`)
poll the web UI's monitoring endpoint. Nothing is installed on the
Robinhood host. All thresholds live in Checkmk; Robinhood's alert
policies are used as classifiers only.

### Packaging

- **Source RPMs**, `lamplighter` and `lamplighter-webui`.
  `rpmbuild --rebuild` builds them on a Linux host. Rocky Linux 9 is the
  reference. The sources are architecture-neutral; the 1.0.0 packages
  were built and tested on ARM64 (aarch64). The engine's Lustre package
  needs Lustre's client development packages on the build host; the
  release check built the POSIX package.
- **The web UI's RPM** carries its own Python environment and the built
  front end. Node.js is needed only on the build host.

### Scale: what was measured, and where

Lamplighter is designed for Lustre filesystems beyond 100 PB (5 to 20
billion entries). **Every measurement so far comes from a development
environment on a laptop, with synthetic databases of up to 10 million
entries. None was made on production hardware or on a real Lustre
filesystem.** Treat the figures below as evidence of the design, not as
production guarantees. `lamplighter-scale` reproduces them on your own
hardware.

At 10 million entries:
- **Web pages** take 0 to 10 ms, and do not grow with the number of
  files.
- **The collector** went from 381 s to 41 s, with:
  - parallel scans by primary-key range
  - a scratch schema for the parallel directory rollup
  - the optional replica index on `NAMES (id, parent_id)`
- **Whole-filesystem search** by owner and size takes 0.38 s with the
  replica indexes (`lamplighter-config replica_indexes`). Without them,
  it stops at its 20-second limit.
- **Jobstats storage** was tested at 2,000 running jobs on 300 targets:
  0.3 s to store a snapshot, and 0.12 to 0.64 s for the triage view.

**Projected for 10 billion entries** (not measured): the replica indexes
need about 2.5 TB and a build of many hours.

`doc/webui-design.md` (Scale) has the details.

### Known limits

- **Production hardware:** see above. Testing at production scale is
  planned after 1.0 (`doc/BACKLOG.md`, item 6).
- **The engine is Robinhood 3.2.0's C code**, unchanged. An
  incremental move to Rust is planned after 1.0 (`doc/BACKLOG.md`,
  item 2).
- **The web UI reads Robinhood's database and never changes it.**
  Running policies or deleting data from the UI is out of scope.

### Licences

- The Robinhood engine and its tools remain under **CeCILL-C**.
- The web UI and the Checkmk plugin are **GPL-2.0-or-later**.
- All dependencies are GPLv3-compatible.
