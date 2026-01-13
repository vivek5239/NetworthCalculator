@echo off
cd /d "C:\Users\Vivek\OneDrive\Work\Code\Repo\financeapp_py_clone"
".\venv\Scripts\uvicorn.exe" api:app --host 0.0.0.0 --port 8000 --reload
pause
