#!/bin/bash
set -e
cd "$(dirname "$0")"

# The local backend reads Tencent Cloud data and holds its authentication.
export DASHBOARD_COLLECTOR_URL="${DASHBOARD_COLLECTOR_URL:-http://127.0.0.1:8220}"
export DASHBOARD_OVERVIEW_URL="${DASHBOARD_OVERVIEW_URL:-http://127.0.0.1:8220}"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost"
export no_proxy="$NO_PROXY"

exec .venv/bin/python -m streamlit run app.py \
    --server.address localhost \
    --server.port "${DASHBOARD_PORT:-8501}" \
    --server.headless=true
