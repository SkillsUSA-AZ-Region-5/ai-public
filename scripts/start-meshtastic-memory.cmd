@echo off
REM Starts the Meshtastic-only Mem0 service and HTTP MCP bridge for Hermes.
REM Equivalent to: stack meshtastic start
cd /d "%~dp0.."
start "" /min "%~dp0..\memori\.venv\Scripts\pythonw.exe" "%~dp0..\memori\meshtastic_mem0_service.py"
start "" /min "%~dp0..\memori\.venv\Scripts\pythonw.exe" "%~dp0..\memori\meshtastic_memory_mcp.py"
