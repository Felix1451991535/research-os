# V1 UI screenshots (2026-10-18, Linux, Chrome)

Made with `scripts/ui_review.sh <out>`: a throwaway backend + worker on a fresh data dir, seeded through the real
API with SYNTHETIC demo data (`scripts/ui_seed.py`), then every page captured by `scripts/ui_review_shots.py`
(Playwright 1.64 + system Chrome) in desktop light, desktop dark and iPad sizes. No real or private data.

Note: these are full-page captures taken at scroll position 0, so the sticky sidebar ends at the viewport height on
long pages; in a browser it follows the scroll. Not tested on a real iPad (NOT RUN, see docs/V1_ASSESSMENT.md).
