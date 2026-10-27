"""Drive the REAL built UI against a REAL backend + worker and save screenshots for milestone reviews.

Usage (dev tool, not shipped): python scripts/ui_screenshots.py <out_dir> [--base http://127.0.0.1:8765]
Requires: playwright (separate tool venv) and a Chrome binary (CHROME env or /usr/bin/google-chrome).
The backend must already be running against a fresh data dir whose path is given by RESEARCHOS_DATA_DIR.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

PW = "correct-horse-battery"
IPAD_UA = ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 "
           "Mobile Safari/604.1")


def shot(page: Page, out: Path, name: str) -> None:
    page.wait_for_timeout(700)
    page.screenshot(path=str(out / f"{name}.png"), full_page=True)
    print("saved", name)


def run(out: Path, base: str, data_dir: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    chrome = os.environ.get("CHROME", "/usr/bin/google-chrome")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 860}, color_scheme="light")
        page = ctx.new_page()
        page.goto(base + "/")
        page.get_by_label("Setup token").wait_for()
        shot(page, out, "01-setup")
        token = (data_dir / "config" / "setup-token.txt").read_text().strip()
        page.get_by_label("Setup token").fill(token)
        page.get_by_label("Username").fill("researcher")
        page.get_by_label("Password").fill(PW)
        page.get_by_role("button", name="Create account").click()
        page.get_by_role("heading", name="Projects").wait_for()
        shot(page, out, "02-projects-empty")
        page.get_by_role("button", name="New project").first.click()
        page.get_by_label("Title").fill("HAR001 — activity recognition baseline")
        page.get_by_label("Goal").fill("Compare a lightweight 1D CNN with LR/RF on UCI HAR under a subject split: "
                                       "macro-F1, latency, size.")
        shot(page, out, "03-new-project-dialog")
        page.get_by_role("button", name="Create project").click()
        page.get_by_text("HAR001").first.wait_for()
        shot(page, out, "04-projects-list")
        page.get_by_text("HAR001").first.click()
        page.get_by_role("button", name="New note").click()
        page.get_by_label("Note title").fill("Reading plan")
        page.get_by_label("Note text").fill("1. UCI HAR dataset paper (Anguita 2013)\n2. Check subject split protocol\n"
                                            "3. Baselines first")
        page.wait_for_timeout(900)
        shot(page, out, "05-note-editor-draft")
        page.get_by_role("button", name="Save").click()
        page.get_by_text("Reading plan").wait_for()
        shot(page, out, "06-project-detail")
        page.goto(base + "/#/tasks")
        page.get_by_role("button", name="Run worker self-test").click()
        page.get_by_text("system.sleep").first.wait_for()
        page.wait_for_timeout(2500)
        shot(page, out, "07-tasks-running")
        page.goto(base + "/#/settings")
        page.get_by_role("button", name="Pair a device").click()
        page.get_by_text("single use").wait_for()
        shot(page, out, "08-pair-device")
        page.keyboard.press("Escape")
        page.get_by_role("tab", name="Models").click()
        shot(page, out, "09-settings-models")
        page.get_by_role("tab", name="Host").click()
        shot(page, out, "10-settings-host")
        dark = browser.new_context(viewport={"width": 1366, "height": 860}, color_scheme="dark",
                                   storage_state=ctx.storage_state())
        dpage = dark.new_page()
        dpage.goto(base + "/#/projects")
        dpage.get_by_text("HAR001").first.wait_for()
        shot(dpage, out, "11-projects-dark")
        ipad = browser.new_context(viewport={"width": 820, "height": 1180}, device_scale_factor=2, has_touch=True,
                                   is_mobile=True, storage_state=ctx.storage_state(),
                                   user_agent=IPAD_UA)
        ipage = ipad.new_page()
        ipage.goto(base + "/#/projects")
        ipage.get_by_text("HAR001").first.wait_for()
        shot(ipage, out, "12-ipad-portrait-projects")
        phone = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                    storage_state=ctx.storage_state())
        ppage = phone.new_page()
        ppage.goto(base + "/#/tasks")
        ppage.get_by_text("system.sleep").first.wait_for()
        shot(ppage, out, "13-phone-tasks")
        browser.close()


if __name__ == "__main__":
    base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "http://127.0.0.1:8765"
    run(Path(sys.argv[1]), base, Path(os.environ["RESEARCHOS_DATA_DIR"]))
