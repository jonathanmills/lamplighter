# SPDX-License-Identifier: CECILL-C
# Copyright (C) 2026 Jonathan Mills
#
# Lamplighter's Rust workspace (doc/RUST-PLAN.md), built with cargo.
#
# make passes what configure decided (version, release, build date, Lustre,
# FID and changelog support) so the Rust and C tools of one build agree.
# cargo runs in the source directory, where it finds .cargo/config.toml:
# `make dist` writes that file and the vendored crates (cargo vendor) into
# the tarball, so a build from the tarball (and rpmbuild --rebuild) needs
# no network. A git checkout builds online as usual.

EXTRA_DIST = Cargo.toml Cargo.lock crates tools

if RUST

DATE = `date --utc --date @$${SOURCE_DATE_EPOCH:-$$(date +%s)} '+%F %T'`
CARGO_ENV = LAMPLIGHTER_VERSION="$(PACKAGE_VERSION)" LAMPLIGHTER_RELEASE="$(RELEASE)" \
	LAMPLIGHTER_BUILD_DATE="$(DATE)" LAMPLIGHTER_LUSTRE_VERSION="$(LVERSION)" \
	CARGO_TARGET_DIR="$(abs_builddir)/target"
# Offline when building from a tarball with vendored crates. RUST_FEATURES
# comes from configure (Lustre, FID and changelog support).
CARGO_FLAGS = --release --locked $$(test -d "$(srcdir)/vendor" && echo --offline) \
	--features "$(RUST_FEATURES)"

all-local:
	+cd "$(srcdir)" && $(CARGO_ENV) "$(CARGO)" build $(CARGO_FLAGS) --workspace

check-local:
	+cd "$(srcdir)" && $(CARGO_ENV) "$(CARGO)" test $(CARGO_FLAGS) --workspace
	cd "$(srcdir)" && python3 tools/check_licences.py Cargo.toml

clean-local:
	-rm -rf target

dist-hook:
	d=$$(cd "$(distdir)" && pwd) && mkdir -p "$$d/.cargo" && \
	cd "$(srcdir)" && "$(CARGO)" vendor --locked "$$d/vendor" | \
	sed 's|^directory = .*|directory = "vendor"|' > "$$d/.cargo/config.toml"

endif
