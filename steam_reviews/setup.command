#!/bin/bash
set -e
cd "$(dirname "$0")"
mkdir -p work data logs
export TMPDIR="$PWD/work"
export PIP_CACHE_DIR="$PWD/work/pip-cache"
export PYTHONUTF8=1
if [ ! -x .venv/bin/python ]; then
  selected=""
  for candidate in python3.12 python3.13 python3.11 python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert (3,11) <= sys.version_info[:2] < (3,15)' >/dev/null 2>&1; then
      selected="$candidate"
      break
    fi
  done
  if [ -z "$selected" ]; then
    echo "Please install Python 3.12 from https://www.python.org/downloads/ and run again."
    exit 1
  fi
  "$selected" -m venv .venv
fi
.venv/bin/python -c 'import sys; assert (3,11) <= sys.version_info[:2] < (3,15), "Python 3.11-3.14 required; recreate .venv with Python 3.12"'
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
echo "Ready. Run ./start.command or ./start.command --sample"
