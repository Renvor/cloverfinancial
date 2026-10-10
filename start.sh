#!/usr/bin/env sh
# SmartTrade AI - one-click start for macOS / Linux
set -e
cd "$(dirname "$0")"
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
$PY -c "import fastapi, uvicorn" 2>/dev/null || $PY -m pip install -r requirements.txt
$PY -m smarttrade
