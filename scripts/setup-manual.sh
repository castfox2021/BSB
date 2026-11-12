#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
manual_root="$PWD"
if command -v uv >/dev/null 2>&1; then
  manual_uv="$(command -v uv)"
elif [ -x .tooling/bin/uv ]; then
  manual_uv="$manual_root/.tooling/bin/uv"
else
  python3 -m venv .tooling
  .tooling/bin/python -m pip install 'uv==0.13.7'
  manual_uv="$manual_root/.tooling/bin/uv"
fi
export UV_CACHE_DIR="$manual_root/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$manual_root/.python"
"$manual_uv" python install 3.12 --no-bin
"$manual_uv" sync --project services/manual-assistant --python 3.12 --extra dev --locked
npm ci --no-audit --no-fund
printf '\n준비 완료: npm run manual:dev\n'
