#!/usr/bin/env bash
set -euo pipefail
umask 077
set -a
. "$HOME/.env_secrets"
set +a
export SENTIMENT_LLM_BASE_URL="${CITYUCS_LLM_BASE_URL:?CITYUCS_LLM_BASE_URL is not set}"
export SENTIMENT_LLM_API_KEY="${CITYUCS_LLM_API_KEY:?CITYUCS_LLM_API_KEY is not set}"
export SENTIMENT_LLM_MODEL="${SENTIMENT_LLM_MODEL:-CS5351/DeepSeek-V4-Flash}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/llm_label_batch.py" "$@"
