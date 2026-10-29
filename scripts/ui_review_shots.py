"""Capture every page of a seeded throwaway instance (scripts/ui_seed.py output) in desktop light, desktop dark and
iPad sizes, for UI review (docs/UI_POLISH.md) and the release video. Dev tool; needs playwright + system Chrome.

`--zh-text-scan` (M7.3, ADR-248) instead opens every page in the zh-CN UI and lists visible English words (text,
placeholder, aria-label, title) that are not exempt: words that occur in the API's JSON responses for that page
(data, protocol values, model output) and PROPER_NOUNS. Writes <out_dir>/zh-text-scan.txt; exit 1 if any remain.

`--matrix` (M8) captures every page in en and zh-CN at 1440x900, iPad landscape 1024x768 and iPad portrait 768x1024
(<lang>-<view>-<page>.png, viewport-sized plus full page) and runs an overflow check on each: the page must not scroll
sideways and no visible element may stick out of the viewport unless a scrolling ancestor clips it. Writes
<out_dir>/overflow.txt; exit 1 if anything overflows.

Usage: python scripts/ui_review_shots.py <out_dir> <base_url> <seed.json> [--zh-text-scan | --matrix]
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Browser, Page, Response, sync_playwright

PW = "correct-horse-battery"
WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
# Proper nouns, file formats and units that stay in Latin script in the zh UI (ADR-221: proper nouns not translated)
PROPER_NOUNS = {w.lower() for w in (
    "Research", "OS", "ResearchOS", "GitHub", "PDF", "PDFs", "CSV", "TSV", "JSON", "API", "GPU", "CPU", "CUDA", "URL",
    "HTTPS", "HTTP", "TLS", "LAN", "iPad", "Windows", "Linux", "macOS", "LaTeX", "Markdown", "Word", "PowerPoint",
    "WPS", "DOI", "DOIs", "arXiv", "OpenAlex", "Crossref", "Zotero", "BibTeX", "RIS", "PRISMA", "OCR", "PWA",
    "Semantic", "Scholar", "Unpaywall", "PubMed", "Europe", "PMC", "medRxiv", "bioRxiv", "EEG", "ECG", "HRV", "EDF",
    "WFDB", "DICOM", "MNE", "CSP", "SHA", "sha256", "Ed25519", "Idempotency-Key", "ZIP", "ipynb", "Python", "PyTorch",
    "NumPy", "DeepSeek", "Qwen", "GLM", "OpenAI", "Anthropic", "Claude", "CNY", "USD", "FX", "K3", "K8", "QA", "ID",
    "IDs",
    "English",  # the language selector names it in its own language
)}
SCAN_JS = """() => {
  const out = [];
  const visible = (el) => el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const el = n.parentElement;
    if (!el || ['SCRIPT', 'STYLE', 'CODE', 'PRE', 'TEXTAREA'].includes(el.tagName) || !visible(el)) continue;
    if (n.textContent.trim()) out.push(n.textContent.trim());
  }
  for (const el of document.querySelectorAll('[placeholder],[aria-label],[title]'))
    for (const a of ['placeholder', 'aria-label', 'title'])
      if (el.getAttribute(a) && visible(el)) out.push(el.getAttribute(a));
  return out;
}"""


def _strings(v: object) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [t for k, x in v.items() for t in (str(k), *_strings(x))]
    if isinstance(v, list):
        return [t for x in v for t in _strings(x)]
    return []


def english_words(texts: list[str], data_words: set[str]) -> list[str]:
    """Latin-script words in the visible texts that are neither data (seen in an API response) nor proper nouns."""
    found = {w for t in texts for w in WORD.findall(t)}
    return sorted(w for w in found if w.lower() not in data_words and w.lower() not in PROPER_NOUNS)


def zh_text_scan(browser: Browser, base: str, seed: dict[str, object], out: Path) -> int:
    data: set[str] = set()

    def keep(resp: Response) -> None:
        if "/api/" not in resp.url or "json" not in (resp.headers.get("content-type") or ""):
            return
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001 (a body that is gone or not JSON is simply not data)
            return
        data.update(w.lower() for t in _strings(body) for w in WORD.findall(t))

    # Data words are pooled over every response from sign-in on and applied at the end: a page whose queries were
    # restored from the persisted cache fetches nothing itself.
    page = login(browser, base, "desktop", "light", on_response=keep)
    page.evaluate("localStorage.setItem('ros-lang', 'zh-CN')")
    texts: list[tuple[str, list[str]]] = []
    for name, route in pages(seed):
        page.goto(base + route)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(600)
        assert_signed_in(page, name)
        texts.append((name, page.evaluate(SCAN_JS)))
    page.context.close()
    lines, total = [], 0
    for name, visible in texts:
        words = english_words(visible, data)
        total += len(words)
        lines.append(f"{name}: {'clean' if not words else ', '.join(words)}")
    (out / "zh-text-scan.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"zh text scan: {total} English word(s) outside the exempt categories")
    return 1 if total else 0
VIEWS = {"desktop": {"width": 1440, "height": 900}, "ipad": {"width": 1024, "height": 1366},
         "ipad-land": {"width": 1024, "height": 768}, "ipad-port": {"width": 768, "height": 1024}}
MATRIX_VIEWS = ("desktop", "ipad-land", "ipad-port")
OVERFLOW_JS = """() => {
  const W = document.documentElement.clientWidth, SW = document.documentElement.scrollWidth, out = [];
  if (SW > W + 1) out.push(`page scrolls sideways (${SW} > ${W})`);
  const clipped = (el) => {
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const ox = getComputedStyle(a).overflowX;
      if (ox === 'auto' || ox === 'scroll' || ox === 'hidden' || ox === 'clip') return true;
    }
    return false;
  };
  for (const el of document.body.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') continue;
    if ((r.right > W + 1 || r.left < -1) && !clipped(el)) {
      const label = (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40);
      out.push(`${el.tagName.toLowerCase()}.${String(el.className).split(' ').slice(0, 3).join('.')} `
        + `[${Math.round(r.left)}..${Math.round(r.right)}] "${label}"`);
    }
  }
  return out.slice(0, 15);
}"""


def matrix(browser: Browser, base: str, seed: dict[str, object], out: Path) -> int:
    lines, bad = [], 0
    for lang in ("en", "zh-CN"):
        for view in MATRIX_VIEWS:
            page = login(browser, base, view, "light")
            page.evaluate(f"localStorage.setItem('ros-lang', '{lang}')")
            for name, route in pages(seed):
                page.goto(base + route)
                page.reload()
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(600)
                assert_signed_in(page, name)
                problems = page.evaluate(OVERFLOW_JS)
                bad += bool(problems)
                lines.append(f"{lang} {view} {name}: {'ok' if not problems else '; '.join(problems)}")
                stem = f"{lang}-{view}-{name}"
                page.screenshot(path=str(out / f"{stem}.png"))
                page.screenshot(path=str(out / f"{stem}-full.png"), full_page=True)
            page.context.close()
    (out / "overflow.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"overflow check: {bad} page view(s) with overflow")
    return 1 if bad else 0


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


def login(browser: Browser, base: str, view: str, scheme: str,
          on_response: Callable[[Response], None] | None = None) -> Page:
    ctx = browser.new_context(viewport=VIEWS[view], color_scheme=scheme)  # type: ignore[arg-type]
    page = ctx.new_page()
    if on_response:
        page.on("response", on_response)
    page.goto(base + "/")
    page.get_by_label("Username").fill("researcher")
    page.get_by_label("Password").fill(PW)
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("heading", name="Projects").wait_for()
    # The query cache (auth state included) is persisted to IndexedDB at most once a second and restored on reload
    # within its 15 s staleTime: a reload right after signing in would restore the signed-out state.
    page.wait_for_timeout(1500)
    if scheme == "dark":
        page.evaluate("localStorage.setItem('ros-theme', 'dark')")
    return page


def assert_signed_in(page: Page, name: str) -> None:
    """A page that fell back to the sign-in screen would be scanned / captured as if it were clean."""
    if page.locator("input[autocomplete=current-password]").count():
        raise SystemExit(f"{name}: the sign-in screen is shown instead of the page")


def main() -> int:
    out, base, seed = Path(sys.argv[1]), sys.argv[2], json.loads(Path(sys.argv[3]).read_text())
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROME", "/usr/bin/google-chrome"),
                                    args=["--no-sandbox"])
        if "--zh-text-scan" in sys.argv[4:]:
            rc = zh_text_scan(browser, base, seed, out)
            browser.close()
            return rc
        if "--matrix" in sys.argv[4:]:
            rc = matrix(browser, base, seed, out)
            browser.close()
            return rc
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
