# Moving Lamplighter's engine to Rust

Lamplighter's engine is Robinhood 3.2.0's C code, about 75,000 lines.
It is moving to Rust **incrementally, from the outside in**: the least
risky parts first (code that only reads), the riskiest last (policy
actions that delete data). There is no big-bang rewrite.

## Why

- **Memory safety.** The engine runs as root for weeks and parses
  paths, changelog records and configuration. Rust rules out buffer
  overflows, use-after-free bugs and similar memory errors by
  construction.
- **Safer concurrency.** Robinhood's core is a multi-threaded pipeline.
  In Rust, most data races are compile errors, which makes it safe to
  raise concurrency later.
- **Maintainability.** Modern tooling, tests, and a language new
  contributors want to work in.

A straight translation runs at about C speed. Any performance gains
come from redesign that Rust makes less risky.

## Rules (decided 2026-11-28)

- **Prove it, then replace it.** Each Rust piece runs side by side with
  the C code it replaces, on the same input, and must give the same
  output. The C reference is built from the `v1.0.0` tag, so it stays
  fixed and reproducible.
- **The Rust binary simply replaces the C one** once its comparison
  tests pass. The C source can then leave `master`; it stays in the
  history and at `v1.0.0`.
- **Compatibility stays absolute.** The database schema, configuration
  syntax, command names and options, and output formats are unchanged
  (`doc/COMPATIBILITY.md`, `doc/NAMING.md`).
- **One feature branch per stage**, merged to `master` only when that
  stage's comparison tests pass. This is the exception to committing
  straight to `master`.
- **Licence:** Rust code translated from Robinhood is a derived work and
  stays under CeCILL-C, like the engine. Crates must be compatible: MIT,
  Apache-2.0, BSD and the like.
- **Toolchain:** Rocky Linux / RHEL 9's own `rust-toolset`. Crates are
  vendored into the source RPM, so `dnf builddep` followed by
  `rpmbuild --rebuild` works on a build host without internet access.

## Stages

| Stage | Branch | What | Risk |
|---|---|---|---|
| 0 | `rust/0-foundations` | Cargo workspace, build and RPM integration (vendored crates), crate licence check, the side-by-side test harness (C reference from `v1.0.0`) | none: no shipped behaviour changes |
| 1 | `rust/1-config` | The configuration parser (Robinhood syntax), proven against every test configuration and template, then fuzzed | reads text only |
| 2 | `rust/2-lux-du` | `lux-du` in Rust, replacing the C binary | reads the database only |
| 3 | `rust/3-lux-find` | `lux-find` (filters, output formats) | reads the database only |
| 4 | | `lamplighter-report` | reads the database only |
| 5+ | | The changelog reader, then the scanner and the pipeline | writes the database |
| last | | Policy runs and their actions | deletes data |

**Target for 1.1:** stages 0 to 2. Stage 3 is a stretch goal.

Stages 5 and later need a real Lustre test system (not a production
site), and a review by the maintainer of anything that deletes data.
