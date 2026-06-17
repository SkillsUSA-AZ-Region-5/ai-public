@echo off
REM `stack` CLI launcher. Self-locating (%~dp0 = this file's dir = the repo root),
REM so the repo can live anywhere - just put this folder on PATH.
"%~dp0memori\.venv\Scripts\python.exe" "%~dp0manage\stackctl.py" %*
