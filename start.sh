#!/bin/bash
set -e

cd "$(dirname "$0")"
source venv/bin/activate

echo "Starting Mil API..."
# Use 4 workers in production — never --reload (causes brief downtime on file changes)
uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 4 \
  --timeout-keep-alive 65 \
  --limit-concurrency 200
