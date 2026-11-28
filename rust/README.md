# Lamplighter in Rust

Lamplighter's engine is moving from Robinhood's C code to Rust, stage by
stage, from the outside in. The plan, rules and stages are in
[`doc/RUST-PLAN.md`](../doc/RUST-PLAN.md).

## Layout

| Path | What |
|---|---|
| `Cargo.toml`, `Cargo.lock` | The workspace. The lock file is committed, and builds use `--locked`. |
| `crates/lamplighter-common` | Shared pieces: build information and the `--version` banner (identical to the C tools'), and user and group names through NSS |
| `crates/lamplighter-config` | The configuration parser: Robinhood's syntax, with the C parser's trees, line numbers and error messages (a flex emulation and Bison's tables; see doc/RUST-PLAN.md, Stage 1). Then the configuration modules every tool loads (`modules.rs`, `load.rs`), the engine modules' status managers and actions as built-in tables (`smi.rs`), boolean expressions (`boolexpr.rs`) and policy definitions (`policies.rs`) |
| `Makefile.am` | Builds the workspace from the engine's `make`, with what `configure` decided. `make dist` bundles the crates (`cargo vendor`), so builds from a tarball or SRPM work offline. |
| `tools/check_licences.py` | Fails on any crate whose licence is not on the allowlist (run by `make check`) |
| `tools/gen_parser_tables.py` | Copies Bison's parse tables from the committed `src/cfg_parsing/conf_yacc.c` into `lamplighter-config/src/tables.rs`; `--check` verifies the copy |
| `difftest/` | The side-by-side tests: the C reference built from `v1.0.0` against the candidates, on the same data |

## Building and testing

The workspace builds with the engine:

```
./autogen.sh && ./configure && make && make check
```

`configure --disable-rust` is for development only. RHEL / Rocky 9
provide the toolchain as `rust-toolset`.

In the development environment, one command runs the cargo tests, the
licence check and every side-by-side case:

```
webui/devenv/devenv.sh rust-test
```

## The side-by-side tests

`difftest/run.sh` takes four steps:
1. Builds the C engine at the `v1.0.0` tag, cached in
   `/build/reference/v1.0.0`, with a fixed `SOURCE_DATE_EPOCH`.
2. Scans the generated test tree into its own database,
   `robinhood_difftest`. The web UI's dev data is never touched.
3. Builds the Rust candidates with the reference's version and build date.
4. Runs every case in `difftest/cases/*.toml`.

A case gives a reference command and a candidate command, or one per
file (`files = [...]`). The harness
compares their exit code and output, and optionally their standard
error. Log timestamps and process IDs are normalized; output with no
defined order can be compared sorted.

For the configuration parser, `c/cfgdump.c` prints what the C parser
built, and `fuzz_config.py` compares the two parsers on randomly
mutated configurations (`FUZZ_ITERATIONS`, `FUZZ_SEED`). A mismatch is
saved in `/build/fuzz-failures`.

For the configuration modules, `c/cfgload.c` runs the reference's C
modules as `rbh_cfg_load()` does and prints the settings they produced.
It includes the reference's `policy_loader.c` to reach its static
readers, and links jemalloc, as the tools do, so it can dlopen the
reference's engine modules. A case can set environment variables
(`env`): the test suite's configurations load with the policy includes
the test suite builds (`/build/difftest-env`, written by `setup.sh`).

A Rust tool replaces its C counterpart only when its cases are
identical.
