@echo off
REM Consistent backup of OpenWebUI's SQLite DB out of the WSL Docker volume to a
REM Windows folder (timestamped). Uses sqlite's .backup (WAL-safe), then docker cp.
REM Self-locating; WSL distro comes from .env (WSL_DISTRO) with a sane default.
setlocal
set "WSL_DISTRO=Ubuntu-24.04"
if exist "%~dp0..\.env" for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0..\.env") do if /i "%%A"=="WSL_DISTRO" set "WSL_DISTRO=%%B"
rem wmic is gone on current Win11 builds; get the timestamp via powershell instead
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set STAMP=%%I
set "OUT=%~dp0..\backups"
if not exist "%OUT%" mkdir "%OUT%"
rem translate the Windows backups path to its /mnt/... form for docker cp inside WSL
for /f "delims=" %%P in ('wsl -d %WSL_DISTRO% -u root -- wslpath "%OUT%"') do set "WSLOUT=%%P"
wsl -d %WSL_DISTRO% -u root -- docker exec open-webui python -c "import sqlite3; s=sqlite3.connect('/app/backend/data/webui.db'); d=sqlite3.connect('/tmp/owui-backup.db'); s.backup(d); d.close(); s.close()"
wsl -d %WSL_DISTRO% -u root -- docker cp open-webui:/tmp/owui-backup.db "%WSLOUT%/webui-%STAMP%.db"
echo Backup written to %OUT%\webui-%STAMP%.db
endlocal
