@echo off
REM SmartTrade AI - one-click start for Windows
cd /d "%~dp0"
python -c "import fastapi, uvicorn" 2>NUL || python -m pip install -r requirements.txt
python -m smarttrade
pause
