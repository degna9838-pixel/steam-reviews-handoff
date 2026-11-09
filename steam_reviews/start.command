#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import fastapi,uvicorn,httpx,langdetect,simplemma' >/dev/null 2>&1; then
  /bin/bash setup.command
fi
export TMPDIR="$PWD/work"
export PYTHONUTF8=1
exec .venv/bin/python -m cs2_reviews serve "$@"
