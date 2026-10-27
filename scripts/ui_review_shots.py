"""Capture every page of a seeded throwaway instance (scripts/ui_seed.py output) in desktop light, desktop dark and
iPad sizes, for UI review (docs/UI_POLISH.md) and the release video. Dev tool; needs playwright + system Chrome.

Usage: python scripts/ui_review_shots.py <out_dir> <base_url> <seed.json>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

PW = "correct-horse-battery"
VIEWS = {"desktop": {"width": 1440, "height": 900}, "ipad": {"width": 1024, "height": 1366}}


def pages(seed: dict[str, object]) -> list[tuple[str, str]]:
    runs = seed["runs"]
    assert isinstance(runs, dict)
    return [
        ("projects", "/#/projects"),
        ("project-detail", f"/#/projects/{seed['project_id']}"),
        ("doc-editor", f"/#/projects/{seed['project_id']}/docs/{seed['doc_id']}"),
        ("experiments", "/#/experiments"),
        ("plan", f"/#/experiments/{runs['passing']['plan_id']}/1"),
        ("run-passing", f"/#/experiments/tasks/{runs['passing']['task_id']}"),
        ("run-failing", f"/#/experiments/tasks/{runs['failing']['task_id']}"),
        ("studies", "/#/studies"),
        ("study-detail", f"/#/studies/{seed['study_id']}"),
        ("literature", "/#/literature"),
        ("library", "/#/library"),
        ("research", "/#/research"),
        ("tasks", "/#/tasks"),
        ("settings", "/#/settings"),
    ]


def login(browser: Browser, base: str, view: str, scheme: str) -> Page:
    ctx = browser.new_context(viewport=VIEWS[view], color_scheme=scheme)  # type: ignore[arg-type]
    page = ctx.new_page()
    page.goto(base + "/")
    page.get_by_label("Username").fill("researcher")
    page.get_by_label("Password").fill(PW)
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("heading", name="Projects").wait_for()
    if scheme == "dark":
        page.evaluate("localStorage.setItem('ros-theme', 'dark')")
    return page


def main() -> int:
    out, base, seed = Path(sys.argv[1]), sys.argv[2], json.loads(Path(sys.argv[3]).read_text())
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROME", "/usr/bin/google-chrome"),
                                    args=["--no-sandbox"])
        for view, scheme in (("desktop", "light"), ("desktop", "dark"), ("ipad", "light")):
            page = login(browser, base, view, scheme)
            for name, route in pages(seed):
                page.goto(base + route)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(600)
                page.screenshot(path=str(out / f"{view}-{scheme}-{name}.png"), full_page=True)
            page.context.close()
        browser.close()
    print("saved", len(list(out.glob("*.png"))), "screenshots to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
