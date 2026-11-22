@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title RuleForge Acceptance Check
.venv\Scripts\python scripts\acceptance_cli.py
echo.
echo Done. Press any key to close...
pause >nul
