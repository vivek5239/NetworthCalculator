@echo off
set BASE_DIR=%~dp0
cd /d "%BASE_DIR%"
if not exist ".\venv" (
    echo Creating virtual environment...
    python -m venv venv
    ".\venv\Scripts\python.exe" -m pip install -r requirements.txt
)
".\venv\Scripts\uvicorn.exe" api:app --host 0.0.0.0 --port 8000 --reload
pause
