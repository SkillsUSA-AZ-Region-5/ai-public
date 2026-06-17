@echo off
REM Launch Qwen Code CLI against the self-hosted stack (qwen3.6-35b-a3b on LM Studio).
REM Qwen Code speaks the OpenAI API; LiteLLM (:4000) meters + proxies to LM Studio.
REM Its tool-call format is native to the qwen model, so it avoids the "Invalid tool
REM parameters" failures Claude Code hits on local models.
REM
REM Run `stack profile code` first so qwen3.6-35b-a3b is loaded.
REM Telemetry/usage-stats are disabled in %USERPROFILE%\.qwen\settings.json.
REM
REM Usage:  qwen-local            (interactive)   |   qwen-local -p "a prompt"
setlocal
set "KEY="
for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do if /i "%%A"=="LITELLM_KEY_QWEN" set "KEY=%%B"
if "%KEY%"=="" ( echo Could not read LITELLM_KEY_QWEN from .env & exit /b 1 )
REM Pass key + base + model as explicit FLAGS, not env vars: qwen-code auto-loads a .env from the
REM working directory, and many projects (including this repo) define their own OPENAI_API_KEY -
REM that would clobber an env-set key and you'd get a 401. Mixing flags with OPENAI_MODEL env also
REM makes qwen ignore the env model and fall back to its DashScope default (qwen3.5-plus). CLI flags
REM outrank everything, so this is deterministic from any directory. --auth-type openai pins the
REM provider so non-interactive (-p) runs don't error with "No auth type is selected".
qwen --auth-type openai --openai-api-key %KEY% --openai-base-url http://localhost:4000/v1 --model qwen-code %*
endlocal
