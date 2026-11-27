# Lamplighter plugin for Checkmk 2.5

This plugin monitors Lamplighter through the web UI's monitoring API. **Checkmk polls Lamplighter**: a special agent runs on the Checkmk server, and nothing is installed on the Lamplighter host. Lamplighter and its web UI report facts only. Every threshold is a Checkmk rule.

Requires **Checkmk 2.5.0 or later**. Built with Checkmk's own packaging code; licensed GPL-2.0-or-later.

## Services

Each Lamplighter host gets these services:

| Service | What it checks | Default thresholds (all adjustable) |
|---|---|---|
| **Lamplighter** | Database reachable; last scan status, age and errors; entries scanned; changelog backlog (Lustre with changelogs only) | Scan age WARN 5 d / CRIT 8 d; scan errors WARN 1 / CRIT 1000; backlog WARN 1M / CRIT 10M records. Database unreachable is CRIT. |
| **Lamplighter collector** | Time since the last successful collector run; failed runs | WARN 26 h / CRIT 50 h; a failed last run is WARN |
| **Lamplighter usage** | Volume and files; data not read for over 1 year and over 3 years | No levels by default (metrics only); set them under *Lamplighter usage* |
| **Lamplighter fileclass \<name\>** (one per Robinhood fileclass) | Entries and volume of the fileclass, and how many of them a Robinhood alert policy used as a classifier marked `alert` | No levels by default; set them per fileclass under *Lamplighter fileclass* (the rule's service condition picks fileclasses) |
| **Lamplighter policy \<name\>** (one per policy with a status) | Entries per status (e.g. `alert`/`clear`, checksum `ok`/`failed`, HSM `new`/`archived`) | None by default; add levels per status value under *Lamplighter policy* (a status with levels is checked even at 0 entries) |
| **Lamplighter jobstats** (when jobstats are configured) | Metadata requests and bytes per second over the last 5 minutes; jobstats sources not reporting | Sources not reporting WARN 1 / CRIT 3; rates have no levels by default |

Robinhood alert policies are used only as **classifiers**: they mark entries `alert` or `clear` in Robinhood's database, and send nothing. This plugin turns those counts into states, with your thresholds. Fileclass and policy figures come from the nightly collector run.

Metrics: `lamplighter_scan_age`, `lamplighter_scan_errors`, `lamplighter_entries`, `lamplighter_changelog_backlog`, `lamplighter_collector_age`, `lamplighter_bytes`, `lamplighter_files`, `lamplighter_cold_1y_bytes`, `lamplighter_cold_3y_bytes`, `lamplighter_class_entries`, `lamplighter_class_bytes`, `lamplighter_class_alerts`, `lamplighter_status_<status>`, `lamplighter_md_ops_rate`, `lamplighter_read_rate`, `lamplighter_write_rate`, `lamplighter_jobstats_stale_sources`.

## Installing

1. **Keycloak:** create a confidential client (for example `lamplighter-checkmk`) with *Client authentication* and *Service accounts* on and *Standard flow* off. Give its service account the web UI client's `monitoring` role, and add an audience mapper so tokens carry the web UI client in `aud`. The dev realm (`../devenv/keycloak/realm-lamplighter.json`) shows a working example.
2. **Install the package** as the site user: `mkp add lamplighter-0.2.0.mkp && mkp enable lamplighter`. You can also use *Setup › Maintenance › Extension packages* in the web UI.
3. **Store the client secret:** *Setup › General › Passwords*.
4. **Create the special agent rule:** *Setup › Agents › VM, cloud, container › Lamplighter*, or search for "Lamplighter". Enter:
   - the web UI URL
   - the Keycloak token endpoint (`https://KEYCLOAK/realms/REALM/protocol/openid-connect/token`)
   - the client ID
   - the stored secret
   - if your CA isn't in the Checkmk server's trust store, the path to the CA bundle
5. **Set up the host:** create a host for Lamplighter, set *Checkmk agent / API integrations* to *API integrations if configured, else Checkmk agent*, run service discovery, and activate changes.

The client secret is handed to the agent as a **password-store reference** (`--client-secret-id`). It never appears on a command line, in a process list or in the fetcher configuration.

### TLS: Checkmk 2.5 verifies certificates strictly

Checkmk 2.5 runs Python 3.13, which enables strict X.509 verification. It **rejects certificates without an Authority Key Identifier extension**, which some older internal CAs omit. If the agent reports `certificate verify failed: Missing Authority Key Identifier`, reissue the web UI or Keycloak certificate with the AKI and SKI extensions. Don't disable verification.

## Development

The tests run under Python 3.13 against **Checkmk 2.5's real plugin APIs**, copied out of the official image: `cmk.agent_based.v2`, `cmk.rulesets.v1`, `cmk.server_side_calls.v1`, `cmk.password_store.v1_unstable` and `cmk.mkp_tool`. They are never committed. From `../devenv`:

```sh
./devenv.sh checkmk-test   # 16 tests: the special agent against the live dev API, checks, rulesets, secret handling
./devenv.sh mkp            # builds dist/lamplighter-0.1.0.mkp with Checkmk's own packaging code
```

**Limitation:** Checkmk publishes its images for x86-64 only. On Apple Silicon they run under emulation, where a full Checkmk site doesn't start, so the package has **not yet been installed in a running Checkmk site**. Do that once on an x86-64 Checkmk 2.5 test site before production.

Layout (a 2.5 plug-in family, installed under `~/local/lib/python3/cmk_addons/plugins/lamplighter/` on the site):

```
agent_based/lamplighter.py          sections, check plugins, default levels
rulesets/lamplighter.py             special agent rule + threshold rulesets (status, collector, usage, fileclass, policy, jobstats)
server_side_calls/lamplighter.py    rule -> agent command line (secret by reference)
special_agent/agent_lamplighter.py  the agent (standard library only)
libexec/agent_lamplighter           executable wrapper Checkmk runs
```
