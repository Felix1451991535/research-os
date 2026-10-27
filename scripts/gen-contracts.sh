#!/usr/bin/env sh
# Regenerate packages/contracts/openapi.json and the FE types from the live FastAPI schema.
set -e
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
RESEARCHOS_DATA_DIR="$TMP" .venv/bin/python -m researchos.app.cli openapi > packages/contracts/openapi.json
rm -rf "$TMP"
cd apps/web && npx openapi-typescript ../../packages/contracts/openapi.json -o src/api/schema.d.ts
