@echo off
REM Starts the Mem0 memory service (windowless) for OpenWebUI + Cline.
REM Double-click to run, or use it as the action for a logon scheduled task.
REM Self-locating: %~dp0 = this scripts\ dir, so ..\memori is the service dir.
REM Equivalent to: stack mem0 start
cd /d "%~dp0..\memori"
"%~dp0..\memori\.venv\Scripts\pythonw.exe" mem0_service.py
