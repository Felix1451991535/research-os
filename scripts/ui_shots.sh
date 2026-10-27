#!/usr/bin/env sh
# Start a throwaway backend + worker on port 8765 with a fresh data dir, capture UI screenshots, stop both.
# Usage: scripts/ui_shots.sh docs/screenshots/Mx   (needs /tmp/shotvenv with playwright; see REVIEW_M0.md)
set -e
cd "$(dirname "$0")/.."
OUT=${1:?output dir}
D=$(mktemp -d); mkdir -p "$D/config"; printf 'port = 8765\nheartbeat_s = 1.0\n' > "$D/config/researchos.toml"
export RESEARCHOS_DATA_DIR="$D"
.venv/bin/python -m researchos.app.cli serve > "$D/api.out" 2>&1 & API=$!
sleep 4
.venv/bin/python -m researchos_worker.main > "$D/worker.out" 2>&1 & WRK=$!
sleep 2
trap 'kill $WRK $API 2>/dev/null; wait 2>/dev/null' EXIT
rm -rf "$OUT"
${SHOT_PY:-/tmp/shotvenv/bin/python} scripts/ui_screenshots.py "$OUT"
