# Install / reinstall guide

How to set up the whole stack from scratch on a Windows box. The stages are mostly
independent, so for a partial reinstall just jump to the one you need. Plan for
about 1 to 2 hours hands-on, plus model downloads and the (optional, long) MinerU
image builds.

> **Portability:** the repo can live at any path. Scripts and the `stack` CLI find
> themselves, and machine-specific settings (`HOST_LAN_IP`, `WSL_DISTRO`, secrets)
> live in `.env`. The only places you type absolute paths are the ones Windows
> itself needs: the scheduled tasks (stage 8), the PowerShell profile function
> (stage 6), the Cline MCP snippet (stage 9), and the distro name in
> [scripts/wsl-keepalive.vbs](scripts/wsl-keepalive.vbs) if yours isn't `Ubuntu-24.04`.

## 0. Prerequisites

- Windows 10/11 with admin rights, NVIDIA GPU(s) with a current driver.
  `nvidia-smi` must work in PowerShell.
- This repo somewhere on disk, e.g. `C:\Users\<you>\Documents\local-ai-stack`.
- Python 3.12+ on the host (python.org installer, tick "Add to PATH").
- Copy [.env.example](.env.example) to `.env` and fill it in. The comments explain
  how to generate each secret. Your LAN IP goes in `HOST_LAN_IP`, and that's the
  only place it lives; compose feeds it to every container that needs it. Find it with:
  ```powershell
  (Get-NetIPConfiguration | ? { $_.IPv4DefaultGateway }).IPv4Address.IPAddress
  ```

## 1. LM Studio (host inference)

1. Install LM Studio from lmstudio.ai. Enable the `lms` CLI in its settings and
   check that `lms --version` works in a fresh PowerShell.
2. Download the models (Discover tab or `lms get`):
   - `google/gemma-4-26b-a4b-qat` for chat (15.6 GB, fits easily in the 32 GB VRAM)
   - `qwen2.5-1.5b-instruct` for the Mem0 brain
   - `qwen2.5-3b-instruct` for Hermes sub-agents under the code profile
   - `text-embedding-nomic-embed-text-v1.5` for embeddings (84 MB)
3. In the Developer/server settings: start the server on port 1234, enable
   "Serve on Local Network" (binds 0.0.0.0), turn on API key auth and put the key
   in `.env` as `OPENAI_API_KEY`.
4. Open the firewall (admin PowerShell):
   ```powershell
   New-NetFirewallRule -DisplayName "LM Studio API" -Direction Inbound -Protocol TCP -LocalPort 1234 -Action Allow
   ```
5. Load the daily models and check the GPU actually took them:
   ```powershell
   lms load google/gemma-4-26b-a4b-qat --gpu max --context-length 32768 -y
   lms load qwen2.5-1.5b-instruct --gpu max --context-length 8192 --parallel 1 -y
   lms load text-embedding-nomic-embed-text-v1.5 --gpu max -y
   nvidia-smi    # gemma should account for ~16 GB across the GPUs
   ```

## 2. WSL2 + Docker Engine (no Docker Desktop), needs a reboot

1. Admin PowerShell: `wsl --install -d Ubuntu-24.04`, reboot, create the Linux user.
2. Copy [.wslconfig.template](.wslconfig.template) to `C:\Users\<you>\.wslconfig`,
   then run `wsl --shutdown`. This keeps WSL on NAT networking; mirrored mode
   breaks docker-ce.
3. Install docker-ce inside the distro:
   ```powershell
   wsl -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/<you>/Documents/local-ai-stack/scripts/install-docker-in-wsl.sh
   ```
   The script enables systemd in `/etc/wsl.conf`, but **systemd only activates after a
   restart of the VM**, and docker won't auto-start at boot until it does. So:
   ```powershell
   wsl --shutdown
   # reopen + confirm docker came up on its own (systemd-managed), not just this session:
   wsl -d Ubuntu-24.04 -u root -- bash -lc "systemctl is-active docker && docker run --rm hello-world"
   ```
   `systemctl is-active docker` must say `active`. If it says `inactive`, systemd didn't
   take. Re-run the script, `wsl --shutdown`, and check again.
4. GPU-in-Docker (needed for MinerU-GPU and vLLM):
   ```powershell
   wsl -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/<you>/Documents/local-ai-stack/scripts/install-nvidia-container-toolkit.sh
   ```
5. Create the ext4 data dirs. Container data must not live on /mnt/c, SQLite
   breaks on the 9p filesystem:
   ```powershell
   wsl -d Ubuntu-24.04 -u root -- mkdir -p /srv/local-ai-stack/openwebui /srv/local-ai-stack/qdrant /srv/local-ai-stack/jupyter /srv/local-ai-stack/hf-cache /srv/local-ai-stack/litellm-db
   ```

## 3. Main stack (OpenWebUI, LiteLLM, Qdrant, SearXNG, Jupyter)

```powershell
wsl -d Ubuntu-24.04 -u root -- bash -lc 'cd /mnt/c/Users/<you>/Documents/local-ai-stack && docker compose up -d'
```
Open http://localhost:3000 and create the admin account (the first signup becomes
admin). The LM Studio models should show up in the model picker; web search and
the code interpreter are already wired up by the compose environment.

Checks:
```powershell
wsl -d Ubuntu-24.04 -u root -- docker ps          # 6 containers up
curl http://localhost:4000/health/liveliness      # litellm
curl http://localhost:6333/healthz                # qdrant
```

## 4. Python venv (Mem0 service + stackctl)

```powershell
cd C:\Users\<you>\Documents\local-ai-stack\memori
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -r ..\manage\requirements.txt
```

## 5. Mem0 memory service

1. Start it and check it's healthy:
   ```powershell
   scripts\start-memory-service.cmd
   curl http://localhost:8077/health     # {"ok":true,"engine":"mem0"}
   ```
2. Install the OpenWebUI memory filter. Either run
   `memori\.venv\Scripts\python.exe memori\install_owui_filter.py`, or paste
   [openwebui/memori_filter.py](openwebui/memori_filter.py) into OpenWebUI under
   Admin Panel > Functions > +, save, and enable it. Point the filter valves at
   `http://<LAN-IP>:8077` with your `MEMORI_SERVICE_TOKEN`. Same procedure for the
   optional [openwebui/web_fetch_tool.py](openwebui/web_fetch_tool.py).
3. Open the firewall so containers can reach the service (admin PowerShell):
   ```powershell
   New-NetFirewallRule -DisplayName "Mem0 memory service" -Direction Inbound -Protocol TCP -LocalPort 8077 -Action Allow
   ```

## 6. The `stack` CLI on PATH

1. Add the repo root to the user PATH so [stack.cmd](stack.cmd) resolves:
   ```powershell
   [Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path","User") + ";C:\Users\<you>\Documents\local-ai-stack", "User")
   ```
2. PowerShell also needs a function (a .cmd on PATH isn't enough there). Append
   this to both `Documents\WindowsPowerShell\profile.ps1` and
   `Documents\PowerShell\profile.ps1`:
   ```powershell
   function stack { & "C:\Users\<you>\Documents\local-ai-stack\stack.cmd" @args }
   ```
   If you've never used profiles: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
3. Open a new terminal and `stack status` should render the orange dashboard.

## 7. MinerU (document extraction), optional and a big build

1. Build the images inside WSL. They bake roughly 40 GB of models in, so launch
   detached with `systemd-run` (survives the shell closing) and watch the log:
   ```powershell
   # pipeline image (mineru:latest), build this one first
   wsl -d Ubuntu-24.04 -u root -- systemd-run --unit=mineru-build --collect bash /mnt/c/Users/<you>/Documents/local-ai-stack/mineru/build.sh
   wsl -d Ubuntu-24.04 -u root -- tail -f /mnt/c/Users/<you>/Documents/local-ai-stack/mineru/build.log
   # then the VLM layer (mineru:vlm, built FROM mineru:latest)
   wsl -d Ubuntu-24.04 -u root -- systemd-run --unit=mineru-vlm-build --collect bash /mnt/c/Users/<you>/Documents/local-ai-stack/mineru/build-vlm.sh
   ```
2. Set the auth password. Pick one, store it in `.env` (`MINERU_AUTH_PASS`), then
   hash it and put the hash in [mineru/Caddyfile](mineru/Caddyfile) (both sites):
   ```powershell
   wsl -d Ubuntu-24.04 -u root -- docker run --rm caddy:2 caddy hash-password --plaintext 'YOUR-PASSWORD'
   ```
3. Start it with `stack profile extract` (CPU, coexists with chat) or
   `stack profile extract-gpu` (fast, but unloads the chat model first).
   Gradio UI: http://localhost:7860 and API: http://localhost:8000/docs, both
   behind the basic-auth prompt.

## 8. Scheduled tasks (resilience)

One self-locating command registers hidden logon tasks (`MemoriMemoryService` starts the
Mem0 service; `StackctlWeb` starts the dashboard on :8090; `LMStudioScheduler` starts the
chat/code profile scheduler on :1235; `KimiLazyProxy` starts the lightweight Kimi wake proxy
on :8095 without loading the model; `WSL-KeepAlive` holds the
WSL VM up so Docker/OpenWebUI don't die when WSL idles out). Run it as the logging-in user,
not elevated:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1
```

(`-Remove` deletes them. `-IncludeKimi` also registers the full Kimi backend at logon, which
loads about 339GB into RAM, so leave that off unless it is intentional. Exact copies of the
original definitions are in `scripts/scheduled-tasks/*.xml` if you'd rather import them verbatim.)

## 9. Coding agents (optional)

Full guide: [cline/cline-setup.md](cline/cline-setup.md). In short:

**Cline (recommended for local models):**
1. Install the Cline extension, then merge
   [cline/mcp-settings.snippet.json](cline/mcp-settings.snippet.json) into Cline's
   `cline_mcp_settings.json`. Paths and `MEMORI_SERVICE_TOKEN` must match your box.
2. Cline settings GUI: API provider "OpenAI Compatible". Either
   `http://localhost:4000/v1` + `LITELLM_KEY_CLINE` + model `cline` (via LiteLLM, so
   Cline usage is metered by app) or `http://localhost:1234/v1` + `OPENAI_API_KEY`
   (LM Studio direct); qwen3.6-35b-a3b is best at tool-use.
3. Paste [cline/custom-instructions.md](cline/custom-instructions.md) into Cline's
   custom instructions so it calls `recall_memory` / `record_memory`. Set a distinct
   `MEMORI_PROJECT` per workspace for per-project memory.
4. For a repo you plan to code in, run:
   ```powershell
   stack code init C:\path\to\repo --project repo-name
   stack code doctor
   ```
   `doctor` should pass after `stack profile code`. If it says the chat profile is active,
   switch profiles before starting a coding agent.

**Claude Code against local models:** run [claude-local.cmd](claude-local.cmd) (repo root).
It points `claude` at LiteLLM's Anthropic endpoint. Works, but local models are weak at
Claude Code's tool-use; Cline is the better fit.

## 10. Image generation (ComfyUI + Flux.1-dev, optional)

Local text-to-image, generated from OpenWebUI. Full guide:
[comfyui/COMFYUI.md](comfyui/COMFYUI.md). In short:

1. Download the model to WSL ext4 (~17GB, ungated, no HF token):
   ```powershell
   wsl -d Ubuntu-24.04 -u root -- bash -lc 'mkdir -p /srv/local-ai-stack/comfyui/models/checkpoints && curl -fL -C - -o /srv/local-ai-stack/comfyui/models/checkpoints/flux1-dev-fp8.safetensors https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors'
   ```
2. Build the ComfyUI image (cu128 for Blackwell, ~10-15 min cold):
   ```powershell
   wsl -d Ubuntu-24.04 -u root -- bash -lc 'cd /mnt/c/Users/<you>/Documents/local-ai-stack && docker compose --profile image build comfyui'
   ```
3. Wire the Flux workflow into OpenWebUI's env (writes `COMFYUI_WORKFLOW*` to `.env`):
   ```powershell
   wsl -d Ubuntu-24.04 -u root -- python3 /mnt/c/Users/<you>/Documents/local-ai-stack/comfyui/build-owui-workflow.py
   ```
4. `stack profile image` (Flux on GPU1 + gemma-4-12b chat on GPU0). ComfyUI UI:
   http://localhost:8188. In OpenWebUI, pick `google/gemma-4-12b-qat`, chat, then click
   the image icon. No Admin > Images setup needed; it's all env-driven (see COMFYUI.md).

## 11. LAN exposure, optional

`stack expose` (approve the UAC prompt) sets up the portproxy and firewall rules
for 3000/7860/8000. The port table and the reasoning live in
[NETWORKING.md](NETWORKING.md).

## Verify the whole thing

```powershell
stack status                  # models loaded, 6+ containers up, mem0 up
stack code doctor             # coding path check; pass after stack profile code
curl http://localhost:8077/health
# OpenWebUI: chat answers (GPU-fast), web search returns sources,
#            "remember that I like X" then a new chat recalls it.
# MinerU: browser prompts for login, then the Gradio UI loads.
```

## Backup / restore

Use [scripts/backup-stack.ps1](scripts/backup-stack.ps1) for disaster-recovery data. It writes
a verified bundle under `Documents\ai-stack-backups\stack-<timestamp>\` with `.env`, OpenWebUI
SQLite data, OpenWebUI Knowledge files, Qdrant memory vectors, a LiteLLM Postgres dump, and
Hermes `/root/.hermes` state when present. The bundle contains secrets, so move it only to a
trusted/encrypted offsite destination.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup-stack.ps1
```

Full WSL image export is still the blunt option:
`wsl --export Ubuntu-24.04 backup.tar`. ComfyUI models under `/srv/local-ai-stack/comfyui`
and LM Studio models are large and re-downloadable, so the stack data bundle leaves them out.
