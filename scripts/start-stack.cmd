@echo off
REM Brings up + keeps alive the local AI stack pieces that don't survive idle/reboot:
REM   1) a keepalive so the WSL VM (and thus OpenWebUI + localhost:3000 forwarding) stays up
REM   2) the Mem0 memory service for OpenWebUI
REM The OpenWebUI container itself auto-restarts (restart: unless-stopped) once WSL is up.
REM The netsh portproxy + firewall rule are persistent and need no action here.
REM Self-locating; WSL distro comes from .env (WSL_DISTRO) with a sane default.
setlocal
set "WSL_DISTRO=Ubuntu-24.04"
if exist "%~dp0..\.env" for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0..\.env") do if /i "%%A"=="WSL_DISTRO" set "WSL_DISTRO=%%B"

REM 1) keep the WSL VM running (detached, hidden). While this wsl.exe lives, the VM lives.
start "" /b wsl.exe -d %WSL_DISTRO% -u root -- /bin/sh -c "while true; do sleep 3600; done"

REM 2) start the Mem0 memory service (windowless)
start "" /b /d "%~dp0..\memori" "%~dp0..\memori\.venv\Scripts\pythonw.exe" mem0_service.py

echo Stack keepalive + memory service started.
endlocal
