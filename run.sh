#!/usr/bin/env bash
# Consultant Experience - start script (macOS / Linux)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created .env from the template."
  echo "Add your ANTHROPIC_API_KEY to it, then run this script again."
  echo "Get a key at https://console.anthropic.com/settings/keys"
  exit 1
fi

echo
echo "Consultant Experience -> http://127.0.0.1:${PORT}"
echo "API docs               -> http://127.0.0.1:${PORT}/docs"
echo
exec ./.venv/bin/python -m uvicorn app.main:app --reload --port "${PORT}"
