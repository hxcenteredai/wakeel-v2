#!/usr/bin/env bash
# Launch both the API (8000) and the Streamlit UI (8001) in one container.
set -euo pipefail

# Ingest the corpus if the vector store is empty (idempotent, offline-safe).
python -m app.corpus.ingest || echo "[entrypoint] corpus ingest skipped/failed (non-fatal)"

# Start the API in the background.
python run.py &
API_PID=$!

# Give the API a moment to bind.
sleep 2

# Start the UI in the foreground (PID 1 replacement keeps logs attached).
exec python -m streamlit run run_ui.py \
    --server.port 8001 \
    --server.address 0.0.0.0 \
    --server.headless true
