@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title RuleForge Web
.venv\Scripts\python -m streamlit run ui/app.py
pause
