@echo off
REM Launch Claude Code against the self-hosted LiteLLM gateway (local models on LM Studio).
REM Claude Code speaks the Anthropic API; LiteLLM's /v1/messages endpoint translates it.
REM Reads LITELLM_KEY_CLAUDE (a per-app LiteLLM virtual key) from .env. Models map via the claude-local-* aliases in
REM litellm/config.yaml - edit those to point at whatever model you have loaded.
REM
REM Usage:  claude-local            (interactive)   |   claude-local -p "a prompt"
REM
REM NOTE: local models are far weaker than Claude at agentic tool-use - expect a rough ride.
REM       Cline (pointed at this same gateway) is the more practical local-coding path.
setlocal
set "KEY="
for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do if /i "%%A"=="LITELLM_KEY_CLAUDE" set "KEY=%%B"
if "%KEY%"=="" ( echo Could not read LITELLM_KEY_CLAUDE from .env & exit /b 1 )
set "ANTHROPIC_BASE_URL=http://localhost:4000"
REM Claude Code's "logged in" check wants ANTHROPIC_API_KEY; set both (LiteLLM accepts either).
set "ANTHROPIC_API_KEY=%KEY%"
set "ANTHROPIC_AUTH_TOKEN=%KEY%"
set "ANTHROPIC_MODEL=claude-local-main"
set "ANTHROPIC_SMALL_FAST_MODEL=claude-local-fast"
claude %*
endlocal
