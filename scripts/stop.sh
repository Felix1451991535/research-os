#!/usr/bin/env sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="${RESEARCHOS_DATA_DIR:-$ROOT/data}/run"
for n in worker api; do
  if [ -f "$RUN/$n.pid" ]; then kill "$(cat "$RUN/$n.pid")" 2>/dev/null || true; rm -f "$RUN/$n.pid"; echo "stopped $n"; fi
done
