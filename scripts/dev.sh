#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
root=$PWD
mode=${1:-serve}
case "$mode" in serve|test|seed|build-ui) ;; *) echo "Usage: $0 [serve|test|seed|build-ui]" >&2; exit 2 ;; esac
if [[ ! -x .venv/bin/python ]]; then
    "${PYTHON:-python3}" -m venv .venv
    .venv/bin/python -m pip install -e 'core[dev]'
fi
if [[ "$mode" == build-ui || ( "$mode" == serve && ! -f web/dist/index.html ) ]]; then
    (cd web && npm ci && npm run build)
fi
if [[ "$mode" == build-ui ]]; then exit; fi
cd core
python="$root/.venv/bin/python"
if [[ "$mode" == test ]]; then exec "$python" -m pytest; fi
if [[ "$mode" == seed && ( "${ZADDESH_DEMO_MODE:-}" != true || "${ZADDESH_DATABASE__URL:-}" != sqlite:///*zaddesh-demo.db ) ]]; then
    echo "Set ZADDESH_DEMO_MODE=true and use a separate SQLite file named zaddesh-demo.db before seeding" >&2
    exit 2
fi
"$python" -m alembic upgrade head
if [[ "$mode" == seed ]]; then exec "$python" -m zaddesh_core.seed; fi
exec "$python" -m uvicorn zaddesh_core.main:app --host 127.0.0.1 --port "${ZADDESH_PORT:-8000}" --no-proxy-headers
