@echo off
rem stop.bat          - stop (watchdog restarts it within 5 min)
rem stop.bat -Disable - stop and disable autostart
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1" %*
pause
