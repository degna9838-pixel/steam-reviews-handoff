#!/usr/bin/env bash
set -euo pipefail
umask 077
if [ -f "$HOME/.env_secrets" ]; then
  set -a
  . "$HOME/.env_secrets"
  set +a
fi
BASE_URL="${CITYUCS_LLM_BASE_URL:-${SENTIMENT_LLM_BASE_URL:-}}"
API_KEY="${CITYUCS_LLM_API_KEY:-${SENTIMENT_LLM_API_KEY:-}}"
if [ -z "$BASE_URL" ] || [ -z "$API_KEY" ]; then
  echo "Missing LLM credentials. Put CITYUCS_LLM_BASE_URL/CITYUCS_LLM_API_KEY or SENTIMENT_LLM_BASE_URL/SENTIMENT_LLM_API_KEY in ~/.env_secrets." >&2
  exit 2
fi
export SENTIMENT_LLM_BASE_URL="$BASE_URL"
export SENTIMENT_LLM_API_KEY="$API_KEY"
export SENTIMENT_LLM_MODEL="${CITYUCS_LLM_MODEL:-${SENTIMENT_LLM_MODEL:-CS5351/DeepSeek-V4-Flash}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/llm_calibrate.py" "$@"
