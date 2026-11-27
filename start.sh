#!/usr/bin/env bash
# One-command start: sets up a venv, builds the UI, runs PaperLens on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${PAPERLENS_PAPERS_DIR:-}" ]; then
  if [ -d papers ]; then export PAPERLENS_PAPERS_DIR="$PWD/papers"
  elif ls ../*.pdf >/dev/null 2>&1; then export PAPERLENS_PAPERS_DIR="$(cd .. && pwd)"
  else mkdir -p papers && export PAPERLENS_PAPERS_DIR="$PWD/papers"; fi
fi
export PAPERLENS_DATA_DIR="${PAPERLENS_DATA_DIR:-$PWD/data}"
echo "Papers: $PAPERLENS_PAPERS_DIR"

if [ ! -d .venv ]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r backend/requirements.txt

if [ ! -d frontend/dist ] || [ "${REBUILD_UI:-0}" = "1" ]; then
  (cd frontend && npm install --no-audit --no-fund && npm run build)
fi

cd backend
python -m paperlens ingest
( sleep 2; command -v open >/dev/null && open http://localhost:8000 ) &
exec python -m uvicorn paperlens.app:app --host 127.0.0.1 --port 8000
