"""M6 UI smoke: the doc editor renders, saves, and shows the gate panel against a REAL backend (fresh data dir).
Usage (dev tool): RESEARCHOS_DATA_DIR=<dir> python scripts/ui_m6_smoke.py <out_dir> [base]"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PW = "correct-horse-battery"


def main() -> int:
    out, base = Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8765"
    data = Path(os.environ["RESEARCHOS_DATA_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROME", "/usr/bin/google-chrome"), args=["--no-sandbox"])
        page = b.new_context(viewport={"width": 1366, "height": 900}).new_page()
        page.goto(base + "/")
        page.get_by_label("Setup token").fill((data / "config" / "setup-token.txt").read_text().strip())
        page.get_by_label("Username").fill("researcher")
        page.get_by_label("Password").fill(PW)
        page.get_by_role("button", name="Create account").click()
        page.get_by_role("heading", name="Projects").wait_for()
        page.get_by_role("button", name="New project").first.click()
        page.get_by_label("Title").fill("M6 writing check")
        page.get_by_role("button", name="Create project").click()
        page.get_by_text("M6 writing check").first.click()
        page.get_by_role("button", name="Manuscript").click()
        page.get_by_text("Citation & number check").wait_for()
        page.locator("textarea").fill("# Results\n\nAccuracy was 0.93 in our cohort {{cite:nobody2024x}}.")
        page.get_by_role("button", name="Save").click()
        page.get_by_text("final export blocked").wait_for()
        page.wait_for_timeout(600)
        page.screenshot(path=str(out / "m6-doc-editor-gates.png"), full_page=True)
        page.get_by_role("button", name="Markdown").click()
        page.wait_for_timeout(1200)
        page.screenshot(path=str(out / "m6-final-export-refused.png"), full_page=True)
        txt = page.inner_text("body")
        b.close()
    ok = "free number" in txt.lower() or "has no source" in txt
    print("gate panel shows free number:", ok, "| unresolved citation shown:", "not in the library" in txt)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
