@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0main.py" --status
echo.
echo ---- last 25 log lines ----
powershell -NoProfile -Command "Get-Content -Encoding UTF8 -Tail 25 \"%~dp0app.log\""
pause
