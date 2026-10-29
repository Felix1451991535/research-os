#!/usr/bin/env sh
# UI review / video capture: throwaway backend + worker (port 8766, fresh data dir), seeded through the real API with
# SYNTHETIC data (scripts/ui_seed.py), then every page captured (scripts/ui_review_shots.py). Dev tool, not shipped.
# Usage: scripts/ui_review.sh <out_dir> [--zh-text-scan | --matrix]   (needs /tmp/shotvenv with playwright, Chrome at
#        /usr/bin/google-chrome; --zh-text-scan lists English left in the zh-CN UI instead of taking screenshots;
#        --matrix: en + zh-CN at 1440x900, 1024x768, 768x1024 with an overflow check)
set -e
cd "$(dirname "$0")/.."
OUT=${1:?output dir}; shift
D=$(mktemp -d); mkdir -p "$D/config"
printf 'port = 8766\nheartbeat_s = 1.0\nextra_template_dirs = ["%s"]\n' "$PWD/tests/fixtures/templates" \
  > "$D/config/researchos.toml"
export RESEARCHOS_DATA_DIR="$D"
.venv/bin/python -m researchos.app.cli serve > "$D/api.out" 2>&1 & API=$!
sleep 4
.venv/bin/python -m researchos_worker.main > "$D/worker.out" 2>&1 & WRK=$!
trap 'kill $WRK $API 2>/dev/null; wait 2>/dev/null' EXIT
.venv/bin/python scripts/ui_seed.py http://127.0.0.1:8766 "$D" > "$D/seed.json"
mkdir -p "$OUT"
${SHOT_PY:-/tmp/shotvenv/bin/python} scripts/ui_review_shots.py "$OUT" http://127.0.0.1:8766 "$D/seed.json" "$@"
echo "data dir: $D"
