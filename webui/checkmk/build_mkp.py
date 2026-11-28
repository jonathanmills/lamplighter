#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Build the Checkmk extension package (MKP) with Checkmk's own packaging
code (cmk.mkp_tool, from the Checkmk 2.5 image; see README.md).

Usage: build_mkp.py OUTPUT_DIR
"""

from __future__ import annotations

import sys
from pathlib import Path

from cmk.mkp_tool import PackagePart
from cmk.mkp_tool._mkp import Manifest, create_mkp, extract_manifest

HERE = Path(__file__).resolve().parent
PLUGINS = HERE / "cmk_addons" / "plugins"           # = ~/local/lib/python3/cmk_addons/plugins on a site
VERSION = "1.0.0"
CHECKMK_BUILT_WITH = "2.5.0p21"
CHECKMK_MIN = "2.5.0"


def plugin_files() -> list[Path]:
    return sorted(p.relative_to(PLUGINS) for p in (PLUGINS / "lamplighter").rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts)


def main(out_dir: str) -> int:
    manifest = Manifest(
        title="Lamplighter",
        name="lamplighter",
        description=("Monitors Lamplighter through its web UI's monitoring API: "
                     "database and last scan (age, errors, changelog backlog), the web UI collector, "
                     "and filesystem usage including data not read for 1 or 3 years. A special agent "
                     "polls the API with a Keycloak client-credentials token; all thresholds are "
                     "Checkmk rules. Licence: GPL-2.0-or-later."),
        version=VERSION,
        version_packaged=CHECKMK_BUILT_WITH,
        version_min_required=CHECKMK_MIN,
        version_usable_until=None,
        author="Jonathan Mills",
        download_url="https://github.com/jonathanmills/lamplighter",
        files={PackagePart.CMK_ADDONS_PLUGINS: plugin_files()},
    )
    mkp = create_mkp(manifest, lambda part: PLUGINS, CHECKMK_BUILT_WITH)
    out = Path(out_dir) / f"lamplighter-{VERSION}.mkp"
    out.write_bytes(mkp)
    check = extract_manifest(mkp)                   # read it back with Checkmk's own reader
    print(f"built {out} ({len(mkp)} bytes): {check.name} {check.version}, "
          f"{sum(len(f) for f in check.files.values())} files, requires Checkmk >= {check.version_min_required}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
