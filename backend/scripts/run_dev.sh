#!/usr/bin/env bash
# Start the backend dev server with auto-reload
set -euo pipefail

cd "$(dirname "$0")/.."
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
