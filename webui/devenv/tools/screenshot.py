# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Render web UI pages in headless Chromium and save screenshots.

Run with ./devenv.sh shots. It signs in with the LDAP form as each test
user, captures pages in both themes and at phone width, and reports any
browser console errors (for example CSP violations).

With --docs (./devenv.sh docshots) it renders the README gallery
instead: DOC_SHOTS, a fixed 1440x900 window (not full pages), saved to
/devenv/.shots/docs and copied to doc/screenshots by devenv.sh.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://webui.test:8443"
OUT = Path("/devenv/.shots")

SHOTS = [
    # (user, path, theme, width, name); user None = signed out
    (None, "/auth/login", "dark", 1440, "login"),
    (None, "/auth/login", "dark", 400, "login-phone"),
    ("dave", "/", "dark", 1440, "overview-dark"),
    ("dave", "/triage", "dark", 1440, "triage-dark"),
    ("carol", "/policies", "dark", 1440, "policies-dark"),
    ("carol", "/search?owner=erin&min_size=21474836480&unit=GiB&type=file&go=1", "dark", 1440, "search-dark"),
    ("carol", "/search?owner=frank&name=*.bam&min_size=10737418240&unit=GiB&go=1", "light", 400, "search-phone-light"),
    ("carol", "/policies", "light", 400, "policies-phone-light"),
    ("dave", "/triage?sort=io&window=6h", "light", 1440, "triage-light-io"),
    ("dave", "/triage", "dark", 400, "triage-phone"),
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
    ("carol", "/browse?dir={projects}", "light", 1440, "browse-projects-light"),
    ("carol", "/browse", "dark", 400, "browse-phone"),
    ("erin", "/browse", "dark", 1440, "browse-own-dark"),     # paths.view_own: her data only
    ("carol", "/reports", "dark", 1440, "reports-dark"),
    ("carol", "/reports/{report}", "dark", 1440, "report-dark"),
    ("carol", "/reports/{report}", "light", 400, "report-phone-light"),
    ("carol", "/reports/new", "light", 1440, "report-editor-light"),
    ("dave", "/usage?view=groups", "dark", 1440, "groups-history-dark"),
    ("dave", "/", "blue", 1440, "overview-blue"),
    ("dave", "/usage?view=groups", "blue", 1440, "groups-blue"),
    ("carol", "/reports/{report}", "blue", 1440, "report-blue"),
    ("carol", "/cold", "blue", 1440, "cold-blue"),
    ("dave", "/triage", "blue", 1440, "triage-blue"),
    ("carol", "/reports/new", "blue", 1440, "report-editor-blue"),
    ("dave", "/", "blue", 400, "overview-phone-blue"),
    ("dave", "/admin/audit", "dark", 1440, "audit-dark"),
    ("dave", "/admin/audit?outcome=denied", "light", 1440, "audit-light-denied"),
    ("alice", "/", "dark", 1440, "alice-viewer"),
    ("alice", "/admin/access", "dark", 1440, "alice-forbidden"),
]

# The README gallery (doc/screenshots): sample data only, no browser frame.
DOC_SHOTS = [
    ("dave", "/", "dark", 1440, "overview"),
    ("dave", "/triage", "dark", 1440, "triage"),
    ("carol", "/cold", "light", 1440, "cold-data"),
    ("carol", "/usage", "dark", 1440, "users"),
    ("carol", "/policies", "light", 1440, "policies"),
    ("carol", "/search?owner=erin&min_size=21474836480&unit=GiB&type=file&go=1", "dark", 1440, "search"),
    ("carol", "/browse?dir={projects}", "light", 1440, "browse"),
    ("carol", "/reports/{report}", "dark", 1440, "reports"),
    ("erin", "/browse", "light", 1440, "browse-own"),
    (None, "/auth/login", "dark", 1440, "sign-in"),
]
DOCS = "--docs" in sys.argv
if DOCS:
    SHOTS, OUT = DOC_SHOTS, OUT / "docs"


def to_webp(png: Path) -> None:
    """README images: WebP is much smaller than PNG at the same sharpness."""
    from PIL import Image
    Image.open(png).save(png.with_suffix(".webp"), "WEBP", quality=90, method=6)
    png.unlink()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
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
            if user is None:
                page.goto(BASE + path)
                page.wait_for_timeout(800)
                page.screenshot(path=str(OUT / f"{name}.png"), full_page=not DOCS)
                if DOCS:
                    to_webp(OUT / f"{name}.png")
                ctx.close()
                continue
            page.goto(BASE + "/auth/login")
            page.fill("#username", user)
            page.fill("#password", "password")
            page.click("text=Sign in with directory password")
            page.wait_for_url(BASE + "/")
            page.evaluate(f"localStorage.setItem('lamplighter-theme', '{theme}')")
            if "{report}" in path:
                # the seeded "Weekly cold owners" report (tools/seed_reports.py)
                reps = page.request.get(BASE + "/api/v1/reports").json()["reports"]
                path = path.replace("{report}", str(next(r["id"] for r in reps if r["name"] == "Weekly cold owners")))
            if "{projects}" in path:
                # Entry ids change with every fresh scan: look up /data/projects.
                root = page.request.get(BASE + "/api/v1/browse").json()
                pid = next(d["id"] for d in root["subdirs"] if d["path"].endswith("/projects"))
                path = path.replace("{projects}", pid)
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
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=not DOCS)
            if DOCS:
                to_webp(OUT / f"{name}.png")
            # Layout check: the page body must never scroll sideways.
            if page.evaluate("document.documentElement.scrollWidth > window.innerWidth"):
                # Name the outermost elements that stick out (not those inside
                # a container that scrolls sideways, which is allowed).
                culprits = page.evaluate("""() => {
                  const w = window.innerWidth, out = [];
                  const scrolls = (el) => { for (let p = el.parentElement; p; p = p.parentElement)
                    if (getComputedStyle(p).overflowX !== 'visible') return true; return false; };
                  for (const el of document.querySelectorAll('body *')) {
                    if (el.getBoundingClientRect().right > w + 1 && !scrolls(el)
                        && !(el.parentElement && el.parentElement.getBoundingClientRect().right > w + 1)) {
                      out.push(el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
                        ? '.' + el.className.trim().split(/\\s+/).join('.') : ''));
                      if (out.length >= 3) break;
                    }
                  }
                  return out;
                }""")
                errors.append(f"{name}: horizontal page overflow at {width}px: {', '.join(culprits) or '?'}")
            ctx.close()
        browser.close()
    for e in errors:
        print("ERROR", e)
    print(f"saved {len(SHOTS)} screenshots to {OUT}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
