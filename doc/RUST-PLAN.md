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

| Stage | Branch | What | Risk | Status |
|---|---|---|---|---|
| 0 | `rust/0-foundations` | Cargo workspace, build and RPM integration (vendored crates), crate licence check, the side-by-side test harness (C reference from `v1.0.0`) | none: no shipped behaviour changes | done (merged) |
| 1 | `rust/1-config` | The configuration parser (Robinhood syntax), proven against every test configuration and template, then fuzzed | reads text only | done: see below |
| 2 | `rust/2-lux-du` | `lux-du` in Rust, replacing the C binary | reads the database only | |
| 3 | `rust/3-lux-find` | `lux-find` (filters, output formats) | reads the database only | |
| 4 | | `lamplighter-report` | reads the database only | |
| 5+ | | The changelog reader, then the scanner and the pipeline | writes the database | |
| last | | Policy runs and their actions | deletes data | |

**Target for 1.1:** stages 0 to 2. Stage 3 is a stretch goal.

Stages 5 and later need a real Lustre test system (not a production
site), and a review by the maintainer of anything that deletes data.

## Stage 1: how the parser was made identical

The configuration parser (`rust/crates/lamplighter-config`) translates
Robinhood's flex/Bison parser rather than reinterpreting the grammar.
That matters, because the C parser has behaviour a rewrite would miss:
- **`and` and `or` have no precedence**, and group to the right:
  `a and b or c` means `a and (b or c)`.
- **The scanner is case-insensitive** (`flex -i`): `NOT` and `%INCLUDE`
  are keywords too.
- **A comment on the last line of a file without a final newline is a
  syntax error.**
- **`$VAR/x` stays literal**, while `$VAR` alone is expanded.
- **Values are cut at 1023 bytes and at NUL bytes.**

The design:
- **The scanner** emulates flex's rule selection: longest match, ties to
  the earliest rule, and trailing context counted. It runs on the rules
  of `conf_lex.l`, in order.
- **The parser** runs Bison's algorithm on Bison's own tables, copied
  from the committed `src/cfg_parsing/conf_yacc.c` (Bison 3.0.4) by
  `rust/tools/gen_parser_tables.py`. A check keeps the copy current.
  Because it reads lookahead tokens exactly when Bison does, it gives
  each item the same line number and each error the same message.

The evidence (`devenv.sh rust-test`):
- A C `cfgdump`, linked with the v1.0.0 parser, and the Rust one print
  every name, ID, value, argument, expression and line number,
  `rh_config_Print`'s output, the error message, warnings and exit code.
- They are identical on all 151 test-suite configurations, the 17
  template files, 39 hand-written edge cases and their 17 include
  files, plus the two development configurations, a missing file and a
  directory: 228 comparisons.
- Differential fuzzing compared them on 200,000 mutated configurations:
  byte edits, grammar tokens, splices, and nesting at the parser's
  10,000-entry stack limit. There were no differences. `rust-test` runs
  10,000 mutants each time; `FUZZ_ITERATIONS` raises that.

Stage 1 replaces no binary: the parser is a library, used from stage 2
by the Rust tools.

