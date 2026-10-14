@echo off
title Jarvis HUD
cd /d "%~dp0"
rem Never bill per token: Claude Code runs on the subscription.
set ANTHROPIC_API_KEY=
rem The server opens the browser itself (JARVIS_OPEN / JARVIS_BROWSER in .env).
".venv\Scripts\python.exe" server.py
pause
