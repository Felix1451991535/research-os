#!/usr/bin/env sh
# Research OS installer for Linux. Usage: ./install.sh [install_dir] [--no-start]
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DIR="${1:-$HOME/ResearchOS}"
APP="$DIR/app"; DATA="$DIR/data"
mkdir -p "$APP" "$DATA/config"
for item in services workers templates packages plugins examples scripts tests apps pyproject.toml uv.lock PACKAGE_MANIFEST.json docs; do
  [ -e "$SRC/$item" ] && cp -R "$SRC/$item" "$APP/"
done
command -v uv >/dev/null || { echo "installing uv (https://astral.sh/uv)"; curl -LsSf https://astral.sh/uv/install.sh | sh; PATH="$HOME/.local/bin:$PATH"; }
(cd "$APP" && uv sync --frozen --extra experiments --extra plugins)
[ -f "$DATA/config/researchos.toml" ] || printf 'host = "127.0.0.1"\nport = 8443\n# Optional relay (docs/OPS.md#model-relay): uncomment; key in RESEARCHOS_RELAY_API_KEY\n# relay_base_url = "https://relay.example.com/v1"\n# relay_for = ["anthropic", "openai"]\n' > "$DATA/config/researchos.toml"
export RESEARCHOS_DATA_DIR="$DATA"
"$APP/.venv/bin/python" -m researchos.app.cli migrate
"$APP/.venv/bin/python" -m researchos.app.cli doctor || true
echo "export RESEARCHOS_DATA_DIR=$DATA   # add to your shell profile"
case " $* " in *" --no-start "*) ;; *) RESEARCHOS_DATA_DIR="$DATA" "$APP/scripts/start.sh";; esac
