# Robinhood Web UI: Design Plan

Status: **draft for discussion**. It replaces `web_gui/gui_v3` (PHP).

## 1. Goals

| Goal | Meaning |
|---|---|
| No PHP | Nothing PHP gets installed on any server. |
| Modern, polished UX | The *visual style* of the Checkmk Pro GUI (see section 5.0). Only the look is borrowed, not Checkmk's monitoring concepts. |
| Multi-user auth and ACLs | OpenID Connect login through Keycloak (or a direct LDAP bind), with groups from LDAP (RFC 2307bis or RFC 2307) and ACLs tied to LDAP group membership (section 4). |
| Operational insight | "Why is the filesystem slow?" triage, and cold-data discovery for data managers. |
| Engine untouched | No changes to the C code in `src/`. The UI only reads what Robinhood v3 already writes to MariaDB. |
| Checkmk does all alerting | Robinhood and the UI expose metrics. Checkmk Pro, the site's only alerting tool, holds every threshold and sends every notification (section 7). |

Out of scope for v1: changing data from the UI (deleting, purging, migrating) and running policies from the UI. The UI is read-only and exports lists that other tools act on.

Out of scope permanently: alerting or notification of any kind in Robinhood or the UI. That includes alert states, alert emails and syslog alerts.

## 2. The constraint that shapes everything

Robinhood's main table `ENTRIES` has one row per filesystem entry, so billions of rows at our scale. Robinhood itself creates essentially **one useful index on it: `parent_id`** (see `src/include/db_schema.def`). `last_access`, `last_mod`, `size` and `uid` are **not indexed**.

What that means:

- A query like "files not accessed in 3 years, largest first" is a **full table scan** over billions of rows. That is fine for a nightly job and unacceptable for a page load.
- Adding indexes to `ENTRIES` would slow the engine's write path, which runs the changelog and scan updates. Robinhood also checks its own schema at startup.
- Robinhood already keeps cheap aggregates:
  - `ACCT_STAT`: per uid/gid/type counts, sizes and size profiles, maintained by triggers. Reads are instant.
  - `VARS`: health counters such as `CL_LastRead`, `CL_LastCommit`, `CL_LastCleared` and the last scan times.
  - `NAMES`: name and parent links, indexed. Directory browsing is cheap through `parent_id`.

**Design response:** the web UI never runs unbounded queries against `ENTRIES` while a page loads. Instead:

1. **Live reads** use only cheap, indexed or aggregated sources: `ACCT_STAT`, `VARS`, and `NAMES`/`ENTRIES` by `id` or `parent_id`.
2. A **collector** (a Python batch job, run on a timer) scans `ENTRIES` once per cycle, ideally on a **replica**. It writes pre-computed rollups into a separate `lux_webui` schema that the UI owns. Age histograms, cold-data rankings and per-directory trees all come from these rollups.
3. The collector also **snapshots** `ACCT_STAT` each cycle, giving per-user and per-group usage history. Robinhood itself only stores current values.

```
Lustre ──changelogs──▶ robinhood daemon ──▶ MariaDB (robinhood schema, engine-owned)
                                                │
                                   (replica)    │ read-only
                                                ▼
                               lux-collector ──▶ MariaDB (lux_webui schema: rollups, history)
                                                │
Lustre jobstats ─▶ jobstats collector ──────────┤
Slurm (slurmrestd) ─────────────────────────────┤
                                                ▼
                       lux-webui API (Python) ◀──▶ browser (static SPA)
                                ▲
                     OIDC IdP ──┘   (behind nginx, TLS)
```

## 3. Technology stack (recommendation)

| Layer | Choice | Why |
|---|---|---|
| API server | **Python 3.12 + FastAPI**, served by Uvicorn/Gunicorn under systemd | Python matches the fork's "modernize toward Python" goal. FastAPI is typed, fast, generates OpenAPI docs, and Rocky 9 ships Python 3.12. |
| DB access | **SQLAlchemy Core** (no ORM) + `mariadb` or `PyMySQL` driver | Hand-written, reviewable SQL against a schema we don't own. Read-only accounts. |
| Collector | Python, same package, run by a **systemd timer** | Same codebase and configuration, no extra runtime. |
| Front end | **TypeScript + React** with React Router, built with **Vite** into static files that the API server serves (one origin, one session cookie) | Large ecosystem for dense data UIs. Node.js runs **only in a build container**: never on developer Macs or servers. |
| UI kit | **No component library.** Hand-written CSS built on the palette tokens (`webui/frontend/src/styles/tokens.css`). **Apache ECharts** for charts. **TanStack Table** later, when tables need virtual scrolling. | Exact control over the Checkmk-style density, the palette and the layout rules, with fewer dependencies. Fonts are the system stack, with Ubuntu first where it is installed; bundling a web font is a separate licensing decision. |
| Web server | **nginx**: TLS termination, serves static assets, reverse proxy to the API | Standard, fits SELinux and FIPS-mode setups. |
| Packaging | **RPM** (`lamplighter-webui`), alongside the existing RPMs | Same deployment path as the rest of Robinhood. |

A lighter alternative is server-rendered HTML with htmx (no JavaScript build step at all). It would help if npm supply-chain policy is a problem, at the cost of a less rich interface. Decide before Phase 1.

## 4. Authentication, directory and access control

**This is a core design goal.** The UI is a true multi-user application. *Who you are* is established by login. *What you may do* is decided by ACLs tied to your **LDAP group memberships**, which follows the same model as Checkmk's user and role management.

Three concerns are kept separate:

| Concern | Source | Notes |
|---|---|---|
| **Authentication** (login) | **OIDC via Keycloak** (preferred), or a **direct LDAP bind** (optional, configurable) | Either method can be enabled, or both. |
| **Directory** (user identity and groups) | **LDAP**, always | Group memberships always come from LDAP, whichever login method was used. |
| **Authorization** (ACLs) | Roles and permissions in the UI, **assigned to LDAP groups** | Managed in the UI's admin pages and stored in `lux_webui`. |

### 4.1 Authentication

**OIDC (Keycloak).** This is the preferred method: the UI never sees user passwords, and users get single sign-on and multi-factor authentication through Keycloak.
- Authorization Code flow with PKCE, implemented **in the back end** (a "backend-for-frontend"). Tokens never reach the browser. The browser holds only an `HttpOnly; Secure; SameSite=Lax` session cookie containing a random session id. `Lax` rather than `Strict` is required, because the return from Keycloak is a cross-site navigation that must carry the cookie. Cross-site writes are refused by an Origin check. Library: Authlib.
- `lux-webui` is a **confidential** Keycloak client with standard flow and PKCE (S256) enabled, and redirect URIs restricted to the UI's hostname. The endpoints come from the realm's `.well-known/openid-configuration` discovery document.
- The logged-in user is mapped to the LDAP directory through a configured claim, `preferred_username` by default, matched against the LDAP `uid`. No roles are needed in Keycloak.
- **Logout** uses RP-initiated logout (Keycloak's `end_session_endpoint`). Back-channel logout should also be enabled, so ending a session in Keycloak ends the UI session.

**Direct LDAP bind (optional).** For sites or situations without Keycloak:
- The user submits a username and password over HTTPS. The back end finds the user's DN with the service account, then **binds as that DN** to verify the password. Passwords are never stored or logged.
- Login rate limiting and lockout logging apply.
- It is off by default when OIDC is configured.

No local accounts, except an optional break-glass admin account that is off by default.

### 4.2 LDAP directory

Feature parity with Checkmk's LDAP connection settings is the target.
- **Transport:** `ldaps://` or StartTLS, with **certificate verification required** and a configurable CA bundle. Plain `ldap://` is refused unless explicitly allowed for testing.
- **Server failover:** a list of servers, tried in order. Connect and response timeouts are configurable.
- **Service account:** a bind DN, with the password read from a root-only file and never kept in the main configuration file.
- **Directory types:**
  - **RFC 2307bis** (this site: OpenLDAP with `groupOfNames` groups, users carrying `uid` and `memberOf`).
  - **RFC 2307** (`posixGroup` with `memberUid`).
  - **Active Directory.**
  Each type sets sensible defaults for the attributes and filters below.
- **Users:** user base DN, scope (`sub` or `one`), user filter (for example `(&(objectClass=posixAccount)(uid={username}))`), and attribute mapping for the login ID (`uid`), display name (`cn`), e-mail (`mail`) and the Unix `uidNumber`/`gidNumber`. The Unix IDs link a person to their files in Robinhood.
- **Groups:** group base DN, scope, group filter (for example `(objectClass=groupOfNames)`), and the membership source, which is either the user's `memberOf` attribute or a search for groups whose `member` contains the user's DN. Nested groups are optional.
- **Paged searches** for large directories, and a **cache** of user and group lookups with a short configurable lifetime (for example 5 minutes), refreshed at login.
- An admin **Directory page** (`/admin/directory`, permission `admin.ldap`; built) shows:
  - the effective LDAP settings, with defaults resolved and secrets never shown;
  - a step-by-step **connection test** per server (TLS, certificate subject, issuer and expiry, service bind, user and group searches), with plain-language errors;
  - a **user lookup** that shows the DN, attributes, every group, and which mapping produced each role;
  - a check that every configured role group exists, with its member count.
  Every test and lookup is audited.
- Library: `python-ldap`, whose permissive license avoids the GPLv2 compatibility questions raised by `ldap3` (LGPLv3); see section 10.

Example (site-specific values are placeholders):

```yaml
ldap:
  servers: [ldaps://ldap1.example.org, ldaps://ldap2.example.org]
  ca_file: /etc/pki/tls/certs/site-ca.pem
  bind_dn: cn=lux-webui,ou=services,dc=example,dc=org
  bind_password_file: /etc/lux-webui/ldap.secret
  directory_type: rfc2307bis
  users:
    base_dn: ou=people,dc=example,dc=org
    filter: (&(objectClass=posixAccount)(uid={username}))
    attrs: {login: uid, name: cn, mail: mail, uidnumber: uidNumber, gidnumber: gidNumber}
  groups:
    base_dn: ou=groups,dc=example,dc=org
    filter: (objectClass=groupOfNames)
    membership: memberOf        # or: member_search
    nested: false
```

### 4.3 Access control (ACLs)

The model follows Checkmk's: **permissions** are grouped into **roles**, and roles are **assigned to LDAP groups**. A user's effective rights are the union over all their groups.

- **Permissions** (a fixed list defined in code), for example:
  - `dashboard.view`
  - `usage.view_aggregates`
  - `usage.view_all_users`
  - `paths.view_own`, `paths.view_all`
  - `cold.view`, `cold.export`
  - `triage.view`
  - `collector.status.view`
  - `admin.ldap`, `admin.acl`, `admin.audit.view`
- **Built-in roles**, editable and cloneable, plus custom roles:

  | Role | Typical permissions |
  |---|---|
  | `viewer` | Dashboards and aggregates; no file paths outside their own data |
  | `user` (self-service) | Their **own** usage, cold data and files only |
  | `data-manager` | Cold-data explorer, owner details, exports of candidate lists |
  | `admin` | Everything, including triage, collector status, LDAP settings and ACL management |

- **Data scope** is the second axis, separate from permissions. A role can be restricted to the user's own files (by `uidNumber`), to the user's Unix groups (`gidNumber`), or to specific path prefixes or projects. So a group's PI can see only their project's data.
- **Mapping:** LDAP group DN to one or more roles, managed in the UI's admin pages and stored in `lux_webui`. A bootstrap admin group is set in the configuration file, so the first login can configure the rest.
- **Enforcement:** permissions are checked in the API, on every endpoint and query. The front end only hides what you can't use; it doesn't enforce anything.
- **Audit log:** every login, permission denial, lookup and export is recorded with user identity and client address, as structured JSON to journald (the authoritative copy for the site's log collection). A copy also goes to `lux_webui.audit_log` through a background writer, so admins can search it on the **Audit log page** (built; `admin.audit.view`). That copy is pruned after `collector.audit_retention_days`. Routine successful monitoring polls are not audited (one a minute would bury real events); refused ones are.
- **Access control page** (built; `admin.acl`): a read-only roles × permissions matrix in plain words, and each mapped LDAP group with its members resolved to login names. Role mappings stay in the config file for now. *Open question:* make them editable in the UI (stored in `lux_webui`, audited, with the config file as a fallback)?

**Machine clients** (Checkmk) don't go through LDAP. They use the separate Keycloak client-credentials client with a `monitoring` permission only (section 7.4).

The old GUI's `ldapauth` and `netauth` plugins are not carried over; this design replaces them.

### 4.4 Implementation is original
Checkmk's LDAP connector is part of the commercial Pro edition, not the GPLv2 Community code, so it is not copied or adapted. Checkmk's *documented features and settings* serve only as a functional reference. The implementation is written fresh on standard Python LDAP libraries.

## 5. Views (feature plan)

### 5.0 Visual style
Modelled on the appearance of the Checkmk Pro GUI:
- A compact, collapsible left navigation sidebar, with the main content area taking up most of the screen.
- High information density: compact tables with small, legible type and tight row spacing, so a lot of data fits on screen without feeling cluttered.
- A restrained, mostly neutral palette with a single accent color, and polished light and dark themes.
- Dashboards built from rearrangeable panels (charts, tables, key numbers), each one clicking through to a detailed view.
- A consistent page header with breadcrumbs, a page-level action menu, and quick filters.
- A global search box for jumping to a user, group, project or path.

### 5.0.1 Page structure and layout rules
- **No HTML frames, ever.** No `<frame>`, `<frameset>` or `<iframe>`. (The original Robinhood GUI used a frameset, and `gui_v3` imitates one with an independently scrolling sidebar.)
- **Whole pages.** Every view is a complete page with its own URL: back and forward work, views can be bookmarked and shared, and a reload returns to the same place.
- **One scroll per page.** The sidebar and header may be sticky but are never separate scrolling regions. Only wide tables and code blocks scroll inside their own containers.
- **Publishing-industry panel layout:**
  - Panels sit on a column grid with one gutter size used everywhere, and edges align to shared grid lines.
  - Content sizes panels: there are no fixed-height boxes. Panels in a row equalize height so their bottom edges align.
  - **Never trap whitespace.** No empty pockets inside or between panels. A lone panel at the end of a row spans the row, and the grid packs panels to close gaps.
  - Whitespace is active: it lives in margins and between groups, where it separates sections.
  - At narrow widths, panels reflow and stack in reading order rather than shrinking into slivers.

### 5.1 Overview (landing page)
- Filesystem capacity and inode usage, plus growth trend (from snapshots).
- **Robinhood status**: changelog backlog (`CL_LastRead` versus `CL_LastCleared`, age of the oldest pending record), last full scan, recent policy runs, and when the collector last ran.
- Top users and groups by volume and file count (`ACCT_STAT`).

### 5.2 Users and groups (built)
- Users and Groups tabs: sortable, searchable dense tables from `ACCT_STAT`, with names, e-mail and group descriptions from LDAP (one cached, paged bulk search each), the share of small files (under 32 KiB), and the change since the last collector snapshot.
- A per-user page at `/usage/user/<uid>`: totals, data by last access, files by size, volume over time (snapshots, then the live figure) and the groups the data is charged to.
- Scope: without `usage.view_all_users`, a person sees only their own row and page. The API enforces this.
- Top directories per user arrive with the per-directory rollups (section 6.3).

### 5.3 Cold-data explorer (data managers)
- Age-bucket heatmap: **bytes by last_access or last_mod age** (for example <30d, 30–90d, 90d–1y, 1–3y, >3y) × owner, group or project.
- Ranked **cold directories**: subtrees whose newest access is older than N, sorted by bytes. This is where whole datasets are found, rather than single files.
- For each candidate: owner, group, project, size, file count, oldest and newest access.
- **Export** a candidate list (CSV/JSON) for owner contact or offload tooling, such as migration to cold storage or Glacier.
- Later: owner notification drafts, and tracking a candidate's state (contacted, approved, offloaded).

### 5.4 Triage ("the cluster is slow")
What Robinhood can and cannot say:
- It can show **what changed recently**: files and directories by owner with recent `last_mod` or `md_update`, and bursts of metadata activity (changelog rate from `VARS`).
- It **cannot** show live I/O. Lustre updates atime lazily, and ATIME changelog records are off by default. Real I/O by job comes from **Lustre jobstats** (`jobid_var=SLURM_JOB_ID`), collected from the metadata and storage servers.

The view combines the two:
- Top jobs by metadata operations and by read/write bytes (jobstats), joined to the **Slurm** job's user, account, nodes and working directory (via `slurmrestd`), joined to that user's or directory's footprint in Robinhood.

This needs a jobstats collector on or near the Lustre servers. It is Phase 3, and could reuse an existing exporter if the site already collects jobstats.

### 5.5 Browse and search
- Directory tree browser, cheap through `parent_id` and `NAMES`, showing size and count per subtree from the rollups.
- Lookup by path, or by owner within a subtree. Bounded queries only, with pagination and time limits.

## 6. Collector design (`lux-collector`)

Status: **v1 implemented** (`webui/backend/src/lux_webui/collector.py`), covering the age histogram and the `ACCT_STAT` history. Directory rollups are still to be designed (6.3).

### 6.1 How a run works
- The collector runs from a timer (nightly), never from a web request. Only one collector runs at a time (MariaDB `GET_LOCK`), and each run is recorded in `collector_runs`.
- **Age histogram: one scan, aggregated by MariaDB.** The collector sends a single `SELECT … GROUP BY uid, gid, access_band, modify_band` over `ENTRIES`, with the age bands computed in SQL `CASE` expressions. MariaDB reads every row once, and only a few thousand summary rows come back, whatever the size of the filesystem. Python never streams the entries themselves.
- The scan runs under `max_statement_time` (setting `collector.max_scan_seconds`, 6 h by default), so a runaway scan aborts instead of loading the server indefinitely.
- **`ACCT_STAT` snapshot:** a cheap copy each run (Robinhood's triggers keep `ACCT_STAT` current), which gives usage history over time.
- **Retention:** the newest `keep_runs` runs are kept, and their rows are deleted with them (`ON DELETE CASCADE`).
- **Where it runs:** point `database` at a **read replica**. A long consistent read on the primary holds InnoDB's undo history while Robinhood writes changelog updates.

### 6.2 Schema (`lux_webui`, managed by numbered migrations in `lux_webui/migrations/`)
- `collector_runs`: start and finish times, status, file count, error.
- `age_hist`: run × uid × gid × access band × modify band → files, bytes, for regular files. Bands: < 7 d, 7–30 d, 30–90 d, 90 d–1 y, 1–3 y, > 3 y. Storing the joint distribution means "written long ago but still read" can also be answered.
- `acct_history`: run × uid × gid × type → entries, bytes, blocks.

The web UI's database user has `SELECT` on Robinhood's schema and full rights on `lux_webui` only. Robinhood's own tables are never written.

### 6.3 Per-directory rollups (built, Phase 3)
Implemented in `lux_webui/rollup.py`, run by the collector on its source connection:
1. A **recursive query** (`WITH RECURSIVE`, MariaDB 10.2+) walks only *directories*, through the `NAMES.parent_id` index, from the root down. The root is found as the parent that is nobody's child. Each directory is mapped to its ancestor at `collector.rollup_depth` (default 4), or to itself when shallower. The result goes into a **TEMPORARY table**, which works on a read-only replica.
2. **One `GROUP BY` over the files** gives totals per tracked directory: files, bytes, newest and oldest access, and bytes not read for 1 and 3 years. A second `GROUP BY` per (directory, owner) gives the top owner.
3. Python adds the totals up the tracked tree. Only directories at depth ≤ `rollup_depth` reach Python. Results go in `dir_rollup` (migration 003).

**Database privilege:** the collector's database user needs `CREATE TEMPORARY TABLES` on Robinhood's schema, in addition to `SELECT`.

**Scale:** cost grows with the number of *directories* (the recursion), plus one more pass over the files. Measure it on a production-size replica (6.4), and lower `rollup_depth` if needed.

**Browse** uses these totals down to `rollup_depth`. Below that it lists entries live through the `parent_id` index (bounded: 500 sub-directories, the 200 largest files, a 10 s statement limit), with no subtree totals. Browsing requires `paths.view_all`; browsing only one's own files (`paths.view_own`) is not built yet. The **coldest-directories** view ranks the leaves of the rollup tree by cold bytes. Hard-linked files are counted once per name.

### 6.4 Scale test (open)
The one-scan approach needs timing on a production-size copy: the expected cost is one full table scan of `ENTRIES`. Run it on the replica once and record the duration here.

## 7. Checkmk monitoring

**Principle:** Robinhood and the UI **report**, and Checkmk **judges**. All thresholds, alert states, notifications and escalation live in Checkmk Pro. Robinhood and the UI publish facts and metrics only.

### 7.1 Robinhood configuration
- Robinhood notifies no one. In the `Log` block, leave `alert_mail` empty and don't route alerts to syslog. On usage triggers, set `alert_high = no` and `alert_low = no`.
- **Policies are the definitions.** Robinhood alert rules and fileclasses (for example "directories with more than 1M entries", "files over 1 TB under /scratch") are still written as normal Robinhood policies, but act only as **classifiers**: the alerter module marks matching entries `alert` or `clear` in the database, and nothing is sent.
- No C changes are needed; this is configuration only.

### 7.2 Services Checkmk sees

| Service | Metrics | Example thresholds (set in Checkmk) |
|---|---|---|
| Robinhood status | Changelog backlog (records), age of the oldest unprocessed record, changelog record rate, time since last scan, daemon running, collector last run | Backlog over 1M records; oldest record over 15 min |
| Policy: *rule or fileclass* (one service per item) | Matching entries, bytes, change since previous run | More than N matches; growth over X per day |
| Usage: filesystem, OST, pool | Used %, bytes, inodes, growth rate | Mirror the purge trigger levels |
| Top users and groups (optional, top N) | Files, bytes, growth per hour or day | Sudden file-count jumps (many-small-files storms) |

Checkmk's predictive levels can be applied to rate metrics to catch "unusual compared with normal" without fixed numbers.

### 7.3 Freshness
- **Cheap and live** (read on every check): Robinhood status from `VARS`, and usage from `ACCT_STAT` and filesystem statistics.
- **From the collector** (updated each collector run, hours apart): per-policy match counts, because counting over all of `ENTRIES` is a full scan. Each metric carries an "as of" timestamp, so Checkmk can flag stale data.

### 7.4 Mechanism
**Design principle: Checkmk polls Robinhood's API.** Nothing is installed on the Robinhood host for monitoring (no Checkmk agent plugin), and Robinhood never pushes to Checkmk.

- **API:** `GET /api/v1/monitoring` on the UI back end. It is read-only and returns JSON of all services and metrics.
- **Machine authentication through Keycloak:** a second Keycloak client, `lux-checkmk`, uses the **client-credentials** grant (a service account) and carries only a `monitoring` role. The Checkmk special agent fetches a short-lived access token and sends it as a Bearer token. The API checks the token's signature against the realm's JWKS, plus its issuer, audience and role. No long-lived static tokens; the client secret is stored in Checkmk's password store. This is independent of how people log in to Checkmk itself (Checkmk's own SSO with Keycloak uses SAML). The special agent is our own code making a standard OAuth 2.0 token request, so the Robinhood side stays entirely OIDC/OAuth 2.0. It must be cheap to call every minute: it reads only live sources and the collector's stored results, and never scans `ENTRIES`.
- **Checkmk plugin (MKP)**, targeting **Checkmk Pro 2.5** (in use: 2.5.0p21):
  - a **special agent**, running on the Checkmk server and configured through `cmk.server_side_calls.v1`, that polls the endpoint;
  - **check plugins** using `cmk.agent_based.v2`, with services discovered per policy, fileclass, OST, pool and user;
  - **rulesets** using `cmk.rulesets.v1`, for the endpoint URL and token, plus threshold rules. Thresholds are set in Checkmk's normal rule screens, so tuning never touches Robinhood.
  - Confirm these API versions against the 2.5 developer documentation before implementing.
- **Early delivery:** the monitoring endpoint can ship before the rest of the UI, as the same back end with only the monitoring routes enabled, so Checkmk can poll it from Phase 2 onward.

## 8. Repository layout

```
webui/
  backend/        # FastAPI app (lux_webui package): api/, auth/, db/, collector/
  frontend/       # React + TS (Vite); `npm run build` → static dist/
  checkmk/        # Checkmk MKP: special agent, check plugins, rulesets
  packaging/      # lamplighter-webui.spec, systemd units, nginx example config
  docs/
web_gui/          # legacy PHP GUI, removed once the new UI covers its features
```

## 9. Phases

| Phase | Delivers |
|---|---|
| 0. Skeleton | Repo layout, FastAPI app, OIDC login against a local Keycloak container (a test realm with the `lux-webui` and `lux-checkmk` clients), an OpenLDAP test container (RFC 2307bis), LDAP directory lookups and the ACL model, nginx config, read-only DB user, dev environment (Linux container with MariaDB and a sample Robinhood DB). |
| 1. Core | Overview and status, Users/Groups from `ACCT_STAT`, collector v1 (`acct_history` and `age_hist`) **with a scale test**. *Done: Overview, collector v1, a first Cold data page (owners with the most cold data, LDAP contact details, audited CSV export), and Users & groups (sortable tables with LDAP names, small-file share and change since the last snapshot, plus a per-user page with age, sizes, history and groups; viewers see only themselves). Still open: the scale test.* |
| 2. Checkmk | *Done: admin pages (Directory, Access control (read-only), Audit log) and the Checkmk 2.5 plugin (`webui/checkmk`: special agent, 3 services, rulesets, MKP), tested against Checkmk's real APIs. Still to do: install on an x86-64 Checkmk 2.5 site; per-policy services need per-policy counts from the collector.* Monitoring endpoint (deployable on its own), Checkmk 2.5 MKP with a polling special agent (Robinhood status and usage services first, then per-policy services from the collector), Robinhood config guidance for classifier-only alert policies. |
| 3. Data managers | Cold-data explorer, directory rollups, exports, role-based access. *Done: directory rollups, Browse (paths.view_all), coldest directories. Still open: own-files browsing (paths.view_own), scale measurement.* |
| 4. Triage | Jobstats collector, Slurm integration, triage dashboard. Rate metrics also feed Checkmk. |
| 5. Retire PHP | Feature parity check against `gui_v3`, remove `web_gui/` from the build and RPM, RPM packaging for the new UI. *Done: `robinhood-webgui` (PHP) removed from the build and spec; `lamplighter-webui` RPM (`webui/packaging`) with venv, systemd units, collector timer and nginx example, tested by installing it in a fresh Rocky 9.7 container behind nginx. Still open: deleting `web_gui/` itself, and an x86_64 build host/CI.* |

## 10. Licensing

The fork is a personal project. The only constraint is Robinhood's CeCILL-C license, which treats two kinds of work differently:

| Code | License | Basis |
|---|---|---|
| **New components** in `webui/` (back end, collector, front end, Checkmk MKP) | **GPL-2.0-or-later** | These are *Related Modules* under CeCILL-C: they add functions without modifying Robinhood's source, so the author chooses their license (CeCILL-C article 6.3). |
| **Changes to Robinhood itself** (anything outside `webui/`, for example a Python port of `scripts/type_gen.pl`, or build changes) | **CeCILL-C** | These are *Integrated Contributions*, so the modified software stays CeCILL-C (articles 5.3.2 and 6.2). |

Why "or later": the "or later" grant lets the code be combined with Apache-2.0 code (Apache ECharts, compiled into the front-end bundle) and with LGPLv3/GPLv3 code. Neither of those combinations is allowed under GPLv2-only. It also remains compatible with Checkmk's GPLv2-only plugin APIs, which the MKP imports.

Rules:
- `webui/LICENSE` holds the GPLv2 text, together with a notice that the code is licensed GPL-2.0-or-later.
- Every new source file starts with `SPDX-License-Identifier: GPL-2.0-or-later` and a copyright line.
- **Dependencies** must use GPLv3-compatible licenses: MIT, BSD, ISC, Apache-2.0, PSF, MPL-2.0, LGPL, or GPLv2+/GPLv3. Check the license when adding any Python or npm dependency. Prefer permissive or LGPL libraries, as with `python-ldap` over `ldap3`.
- **No copied code** from proprietary sources (for example Checkmk Pro). Code from GPL-compatible projects may be reused only with its copyright and license notices kept.

## 11. Open questions

1. **Keycloak clients**: can the Keycloak admins create the `lux-webui` (authorization code) and `lux-checkmk` (client-credentials) clients? Does `preferred_username` in tokens always equal the LDAP `uid`?
2. **Replica**: can the collector read from a MariaDB replica of the Robinhood database?
3. **Scale**: roughly how many entries are in `ENTRIES` now, and how fast does it change? This sets the collector's design budget.
4. **JavaScript tooling policy**: is building a React front end with npm on a build host acceptable, or do we need the no-build htmx option?
5. **Self-service**: should ordinary users log in to see their own data, or is it staff only?
6. **Jobstats**: is jobstats collection already enabled or exported somewhere (for example to Prometheus)?
7. **First policies to monitor**: which conditions caused real slowdowns or data problems recently? Those become the first classifier policies and Checkmk services.
