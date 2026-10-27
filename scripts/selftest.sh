#!/usr/bin/env sh
# Linux counterpart of selftest.ps1: install dev deps, run the full test suite and doctor, write a report.
# Exits non-zero if pytest fails (the report is still written).
set -u
cd "$(dirname "$0")/.."
DATA="${RESEARCHOS_DATA_DIR:-$PWD/data}"; mkdir -p "$DATA/logs"
REPORT="$DATA/logs/selftest-$(date +%Y%m%d-%H%M%S).txt"
RC="$DATA/logs/.selftest-rc"
echo 1 > "$RC"
{ echo "Research OS selftest $(date -Is) $(uname -a)"
  uv sync --frozen --extra experiments --extra dev --extra plugins
  .venv/bin/python -m pytest -q -p no:warnings -o addopts=""; rc=$?; echo "$rc" > "$RC"; echo "pytest exit: $rc"
  .venv/bin/python -m researchos.app.cli doctor; echo "doctor exit: $?"; } 2>&1 | tee "$REPORT"
echo "report: $REPORT"
exit "$(cat "$RC")"
