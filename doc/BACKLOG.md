# Lamplighter backlog (after 1.0)

Ideas for after the 1.0 release. 1.0 is tagged when every web UI
feature and the scale testing are complete. Nothing here is scheduled
or designed yet. Each entry records the idea, why it matters, the
questions to settle first, and how it fits the project's standing rules
(`CLAUDE.md`, `doc/COMPATIBILITY.md`). The order is the order the ideas
were proposed, not a priority.

---

## 1. Blue theme

**Idea:** a third colour theme next to Dark and Light, of medium
intensity between the two. It replaces the purple gradients and greys
with a range of blue gradients.

**Why:** some people find the dark theme too heavy and the light theme
too bright. A blue theme also suits sites whose own branding is blue.

**Questions:**
- Which blues? A ramp built around the existing Carolina Blue accent is
  the obvious starting point (`webui/design/palette-draft.html`).
- The aubergine accents (sidebar wash, login screen, top-bar line, code
  panels) are part of the identity. Do they stay, turn blue, or become
  a navy?
- Charts and status colours must stay distinguishable against a blue
  surface, including for colour-blind readers. Validate the palette;
  don't judge it by eye.

**Fit:** front end only (CSS tokens plus the theme switcher). No back-end
or database change.

---

## 2. Rust, from the outside in

**Idea:** a slow, ongoing rewrite of Robinhood's C core in Rust,
starting with the least risky parts (read-only tools) and ending with
the riskiest (policy actions that delete data). Each step must be
proven identical to the C behaviour.

**Why:**
- **Better threading.** Robinhood's core is a multi-threaded pipeline. In
  Rust most data races and missed locks are compile errors, which makes
  it far safer to raise concurrency.
- **Better performance, possibly,** through rewrites with better
  algorithms, such as batched database writes or more concurrent
  metadata requests. A plain translation would run at about C speed: the
  core mostly waits on the Lustre metadata servers and MariaDB, not on
  the CPU. The gains come from redesign, which Rust makes less risky.
- **Safer memory management.** The core runs as root for weeks at a time
  and parses paths and changelog records. Rust rules out buffer
  overflows, use-after-free bugs and similar memory errors by
  construction. Memory-safe languages are also what CISA and the NSA
  recommend, which counts for security reviews.

**Questions:** all for a dedicated planning conversation first:
- the order of components
- the differential test harness (C and Rust run against the same input
  and their output compared)
- calling Lustre's C library (`liblustreapi`)
- packaging
- feature-branch rules

**Fit:** the database schema, configuration syntax, module names and
the `robinhood` database user never change. Until the plan exists, the
rule "leave the C engine alone" stands.

---

## 3. Policy editor

**Idea:** create, edit and delete policies from the web UI.

**Why:** editing policy files by hand on a server is error-prone, and
mistakes in purge policies are costly. A form with validation, previews
and an audit trail is safer.

**Ideas to explore:**
- **Protected policies.** Policies written in the configuration files
  can't be deleted from the UI, and perhaps not edited there either.
- **A second source of policy definitions:** a database table that the
  editor can rewrite more easily and safely than a text file.

**Questions:**
- **How does the engine learn about edited policies?** Robinhood reads
  its policies from configuration files only. A database source would
  probably mean generating an include file (Robinhood's `%include`) from
  the table and having the engine reload it, without changing the C
  engine. Which settings Robinhood can reload without a restart needs
  checking.
- **Validation and preview:** before saving, show what a policy would
  match (counts from the collector, or a dry run).
- **Who may edit:** a new ACL permission, recorded in the audit log, and
  perhaps a second person's approval for purge policies.
- **Version history and rollback** of edits.

**Fit:**
- Robinhood configuration files must keep working unchanged when copied
  to `/etc/lamplighter.d/`. A database source would be an optional
  Lamplighter addition, never a replacement.
- The web UI reads Robinhood's database and doesn't write to it, so
  policy definitions would live in Lamplighter's own schema
  (`lamplighter_webui`).

---

## 4. Quota emails to users

**Idea:** automatic emails to users who have hit their quota, or who
are close to it.

**Why:** users usually find out they're over quota when a job fails.
An early warning saves compute time and support tickets.

**Questions:**
- **Where do quota figures come from?** Robinhood records usage, not
  Lustre quotas. Quotas would need `lfs quota`, or reading quota
  settings from the Lustre servers, as a new collector input.
- **Thresholds and frequency:** for example 90% and 100%, at most once a
  day, and a "back under quota" message.
- **Email addresses:** from LDAP (the `mail` attribute).
- **Content and opt-out:** what the message says (usage, the largest and
  oldest files, a link to the user's own page), and whether users can
  opt out.

**Fit:** these are informational messages to users about their own
data, not system alerts, so they don't conflict with the rule that
Checkmk Pro is the only tool for fault detection and operational
alerting. Opt-in for the site, off by default, and holding up at large
user counts.

---

## 5. Opening support tickets from the UI

**Idea:** from the web UI, send an email to the site's support address
(for example `support@`) that opens a ticket in its ticket system. Uses
include a filesystem error, or unusual activity by a user that the
sysadmin team needs to investigate.

**Why:** the person who notices a problem in the triage or usage views
has all the context there. A button that sends that context (what was
seen, when, which job, user, directory or target, and a link back to
the same view) saves time and gives better tickets.

**Questions:**
- **Email or API:** email works with almost every ticket system and
  needs no integration. Per-system APIs (Jira, ServiceNow, RT and so on)
  could follow as optional plug-ins, the same way the job scheduler
  plug-ins work.
- **Who may send:** an ACL permission, recorded in the audit log.
- **Privacy:** what may be included about another user's activity.
- **The line with Checkmk:** a ticket that a person opens on purpose is a
  report, not fault detection, so it fits the rules. Automatic tickets
  raised when something looks wrong would be fault detection, and those
  belong to Checkmk.

**Fit:** opt-in and configured in `config.yaml`. No dependency on any
particular ticket system.

---

## 6. Testing on production-class hardware

**Idea:** run the scale harness on a real Lustre filesystem and a
server-class MariaDB replica:
- `lamplighter-scale gen` and `bench`, from 100 million to billions of
  entries
- `lamplighter-scale jobstats` at the site's real job and target counts
- a collector run against a replica of a production Robinhood database

**Why:** every scale figure so far was measured in a development
environment on a laptop, up to 10 million entries. The design targets 5
to 20 billion entries per filesystem, and the projections (index sizes,
build and collector times) need confirming, or correcting, on real
hardware.

**Questions:**
- Permission to run the software at a site, at scale.
- A replica to test against.
- Which figures to publish without identifying the site.

**Fit:** nothing to build. The harness exists (`webui/backend`,
`lamplighter-scale`) and writes JSON results (`--json`) for comparison.
Until these runs happen, the docs and release notes must say that scale
was measured only in a development environment.

---

## 7. Scheduled reports and custom graphs

**Idea:** two features of Robinhood's old PHP GUI (`gui_v3`) that the
new web UI does not have: **report tasks** that run on a schedule and
deliver results (for example a weekly usage summary), and **custom or
stacked graphs** that a user assembles.

**Why:** feature parity with `gui_v3`. They were listed for parity, not
requested, so they wait until someone needs them.

**Questions:**
- **Delivery:** by email (informational, like the quota notices in item
  4) or saved files.
- **What a report contains:** probably the existing exports on a
  schedule.
- **Custom graphs:** do they add enough beyond the fixed charts?

**Fit:** reports are informational messages, not system alerts.
Scheduling would use a systemd timer, like the collector's.
