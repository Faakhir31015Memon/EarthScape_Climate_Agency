#!/usr/bin/env bash
# EarthScape Climate Agency — local development launcher
#
# Usage:
#   ./scripts/dev.sh backend     # Django API on http://127.0.0.1:8000
#   ./scripts/dev.sh frontend    # Vite dev server on http://localhost:5173
#   ./scripts/dev.sh bootstrap   # apply schema/indexes + create first accounts
#   ./scripts/dev.sh seed        # ingest real NASA POWER observations
#   ./scripts/dev.sh check       # compile-check backend + typecheck frontend
#
# Written for Git Bash on Windows; also runs under bash on Linux/macOS.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/.venv/bin/python"

cd "$ROOT"

ensure_env() {
  if [ ! -f "$ROOT/.env" ]; then
    echo "No .env found. Copying .env.example — edit it before connecting real credentials."
    cp "$ROOT/.env.example" "$ROOT/.env"
  fi
}

case "${1:-}" in
  backend)
    ensure_env
    exec "$PY" backend/manage.py runserver 0.0.0.0:8000
    ;;
  frontend)
    cd "$ROOT/frontend"
    [ -d node_modules ] || npm install
    exec npm run dev
    ;;
  bootstrap)
    ensure_env
    exec "$PY" backend/scripts/bootstrap_platform.py
    ;;
  seed)
    ensure_env
    exec "$PY" backend/scripts/seed_real_data.py
    ;;
  check)
    ensure_env
    echo "== backend compile check =="
    "$PY" -m compileall -q backend/common backend/config backend/apps backend/scripts
    echo "== frontend typecheck =="
    (cd "$ROOT/frontend" && npm run lint)
    ;;
  *)
    sed -n '3,11p' "${BASH_SOURCE[0]}"
    exit 1
    ;;
esac
