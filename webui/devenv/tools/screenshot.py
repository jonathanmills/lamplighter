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
    ("carol", "/cold", "dark", 1440, "cold-dark"),
    ("carol", "/cold?older_than=3y", "light", 1440, "cold-light-3y"),
    ("carol", "/cold", "dark", 400, "cold-phone"),
    ("carol", "/usage", "dark", 1440, "users-dark"),
    ("carol", "/usage?view=groups", "light", 1440, "groups-light"),
    ("carol", "/usage/user/bob", "dark", 1440, "user-bob-dark"),
    ("carol", "/usage/user/frank", "light", 1440, "user-frank-light"),
    ("alice", "/usage", "dark", 1440, "users-alice-self"),
    ("alice", "/usage/user/frank", "dark", 1440, "users-alice-forbidden"),
    ("carol", "/usage", "dark", 400, "users-phone"),
    ("dave", "/admin/directory", "dark", 1440, "directory-dark"),
    ("dave", "/admin/directory", "light", 1440, "directory-light"),
    ("dave", "/admin/access", "dark", 1440, "access-dark"),
    ("carol", "/browse", "dark", 1440, "browse-root-dark"),
    ("carol", "/browse?dir=84ABCC3:E8C4", "light", 1440, "browse-projects-light"),
    ("carol", "/browse", "dark", 400, "browse-phone"),
    ("dave", "/admin/audit", "dark", 1440, "audit-dark"),
    ("dave", "/admin/audit?outcome=denied", "light", 1440, "audit-light-denied"),
    ("alice", "/", "dark", 1440, "alice-viewer"),
    ("alice", "/admin/access", "dark", 1440, "alice-forbidden"),
]


def main() -> int:
    OUT.mkdir(exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        # --disable-gpu: avoids an intermittent startup stall of headless
        # Chromium inside containers (no GPU there anyway).
        browser = p.chromium.launch(args=["--disable-gpu"], timeout=30000)
        for user, path, theme, width, name in SHOTS:
            ctx = browser.new_context(viewport={"width": width, "height": 900},
                                      ignore_https_errors=True, device_scale_factor=2)
            page = ctx.new_page()
            page.set_default_timeout(20000)  # fail fast instead of hanging
            def on_console(m, n=name):
                # "*-forbidden" shots exist to show a refused page: their 403 is expected.
                if m.type == "error" and not (n.endswith("-forbidden") and "status of 403" in m.text):
                    errors.append(f"{n}: {m.text}")
            page.on("console", on_console)
            page.on("pageerror", lambda e, n=name: errors.append(f"{n}: {e}"))
            page.goto(BASE + "/auth/login")
            page.fill("#username", user)
            page.fill("#password", "password")
            page.click("text=Sign in with directory password")
            page.wait_for_url(BASE + "/")
            page.evaluate(f"localStorage.setItem('rbh-theme', '{theme}')")
            page.goto(BASE + path)
            page.wait_for_selector(".content")
            if path == "/admin/directory":
                page.click("text=Run test")
                page.wait_for_selector("text=Run again")
                page.fill("#lookup-user", "carol")
                page.click("button:has-text('Look up')")
                page.wait_for_selector("text=Can sign in")
            page.evaluate("window.scrollTo(0, 0)")  # full-page capture from the top
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
