"""Run the SmartTrade AI local server:  python -m smarttrade"""

from __future__ import annotations

import sys

import uvicorn

from .config import settings


def main() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    print("=" * 64)
    print(" SmartTrade AI - local research, backtesting and paper trading")
    print(f" Open your browser at:  http://{settings.host}:{settings.port}")
    print(f" Data directory:        {settings.data_dir}")
    print(" Live trading: DISABLED | Live capital: $0 | No broker connected")
    print(" Press Ctrl+C to stop.")
    print("=" * 64)
    reload = "--reload" in sys.argv
    uvicorn.run("smarttrade.api.app:app", host=settings.host, port=settings.port, reload=reload, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
