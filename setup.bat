@echo off
rem Full install: venv, packages, Windows settings (UAC prompt), autostart
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
pause
