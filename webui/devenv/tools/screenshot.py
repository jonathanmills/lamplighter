# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Render web UI pages in headless Chromium and save screenshots.

Run with ./devenv.sh shots. It signs in with the LDAP form as each test
user, captures pages in both themes and at phone width, and reports any
browser console errors (for example CSP violations).
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://webui.test:8443"
OUT = Path("/devenv/.shots")

SHOTS = [
    # (user, path, theme, width, name)
    ("dave", "/", "dark", 1440, "overview-dark"),
    ("dave", "/", "light", 1440, "overview-light"),
    ("dave", "/", "dark", 1000, "overview-medium"),
    ("dave", "/", "dark", 400, "overview-phone"),
    ("dave", "/cold", "dark", 1440, "cold-placeholder"),
    ("alice", "/", "dark", 1440, "alice-viewer"),
    ("alice", "/admin/access", "dark", 1440, "alice-forbidden"),
]


def main() -> int:
    OUT.mkdir(exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for user, path, theme, width, name in SHOTS:
            ctx = browser.new_context(viewport={"width": width, "height": 900},
                                      ignore_https_errors=True, device_scale_factor=2)
            page = ctx.new_page()
            page.set_default_timeout(20000)  # fail fast instead of hanging
            page.on("console", lambda m, n=name: m.type == "error" and errors.append(f"{n}: {m.text}"))
            page.on("pageerror", lambda e, n=name: errors.append(f"{n}: {e}"))
            page.goto(BASE + "/auth/login")
            page.fill("#username", user)
            page.fill("#password", "password")
            page.click("text=Sign in with directory password")
            page.wait_for_url(BASE + "/")
            page.evaluate(f"localStorage.setItem('rbh-theme', '{theme}')")
            page.goto(BASE + path)
            page.wait_for_selector(".content")
            page.wait_for_timeout(1200)  # let charts finish drawing
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            # Layout check: the page body must never scroll sideways.
            if page.evaluate("document.documentElement.scrollWidth > window.innerWidth"):
                errors.append(f"{name}: horizontal page overflow at {width}px")
            ctx.close()
        browser.close()
    for e in errors:
        print("ERROR", e)
    print(f"saved {len(SHOTS)} screenshots to {OUT}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
