@echo off
chcp 65001 >nul
cd /d "%~dp0.."
.venv\Scripts\python cli.py list
pause
