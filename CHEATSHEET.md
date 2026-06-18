# Operations cheat sheet

Every `stack` command in one place. Full detail lives in [README.md](README.md);
disaster recovery in [RECOVERY.md](RECOVERY.md); SkillsUSA pipeline in
[skills-usa-readme.md](skills-usa-readme.md).

## Power: start / stop the whole box

```powershell
stack shutdown            # graceful stop of ALL apps before powering off (flushes data)
stack shutdown --wsl      # also shut the WSL VM down (if not powering off Windows)
stack shutdown --unload-models   # also free GPU VRAM
stack startup             # bring core containers + mem0 + web back up after a reboot
stack profile chat        # then load the models (LM Studio must be running)
```
> Always `stack shutdown` before a Windows shutdown/reboot. A hard poweroff can kill the
> WSL VM mid-write and corrupt Qdrant / OpenWebUI's SQLite.

## Status & health

```powershell
stack status              # CPU/RAM/GPU, loaded models, containers, host services
stack doctor              # health-check every service (up/down)
stack doctor --fix        # recover whatever's down (restart host services, start containers)
stack monitor --start     # background flight recorder -> manage/stack-monitor.log
stack monitor --status    # show monitor pid/log path
stack monitor --stop      # stop the background recorder
stack code doctor         # coding path: LM Studio, LiteLLM, keys, aliases, Mem0, dashboard
stack code status         # current profile guess + safe aliases
```
Web dashboard for all of this in a browser: http://localhost:8090 (login from `.env`).

## Models & workload profiles

```powershell
stack model ps                       # what's loaded in LM Studio
stack model load <id> --ctx 32768    # load a model (full GPU offload)
stack model unload [<id>]            # unload one, or all if omitted
stack profiles                       # list workload profiles
stack profile chat                   # daily: main(gemma-26b) + memory core + web stack
stack profile code                   # coding agents: qwen3.6-35b-a3b @ 327680 + memory core + Hermes sub-agent model
stack profile image                  # image gen: ComfyUI(Flux GPU1) + gemma-4-12b chat(GPU0/CPU)
stack profile extract                # MinerU on CPU (coexists with chat)
stack profile extract-gpu            # MinerU on GPU (unloads the main model, keeps memory core)
stack profile vllm                   # (BLOCKED on Blackwell/WSL - see note) free GPU for vLLM
```

## LM Studio scheduler (chat/code on demand)

```powershell
stack lm-scheduler start|stop|restart|status|logs
```

LiteLLM routes chat/code-facing aliases through the scheduler on :1235. If the
needed profile is cold, the first request returns a warming error and starts
`stack profile chat` or `stack profile code` in the background. The main model
is loaded with LM Studio `--ttl 5400`, so LM Studio unloads it after 90 idle
minutes.

Smoke test:
```powershell
stack model unload
stack lm-scheduler status
# Then send one LiteLLM chat request to google/gemma-4-26b-a4b-qat and one to
# qwen/qwen3.6-35b-a3b. First cold request should warm the profile; retry should
# return a model response. Confirm `stack model ps` shows TTL on the main model.
```

Kimi smoke:
```powershell
stack kimi status
# First POST to kimi/kimi-k2.7-code should return kimi_warming and start the
# backend. Full Kimi smoke is heavy: about 400s load and about 305GB RAM.
```

**Swap the main chat model** (the Mem0 brain is decoupled, so memory keeps working):
```powershell
stack model load <other-model> --ctx 32768      # e.g. google/gemma-4-31b-qat
stack model unload google/gemma-4-26b-a4b-qat    # free the old main
# leave qwen2.5-1.5b-instruct (brain) + nomic loaded = the "memory core"
```
> **vLLM** currently deadlocks on the RTX 5060 Ti (Blackwell sm_120) under WSL2 - it's an
> upstream Triton issue. Use LM Studio (`:1234`) as the serving endpoint until a newer vLLM
> image fixes it. GPU MinerU likely hits the same wall; use `stack profile extract` (CPU).

## Containers (main stack)

```powershell
stack up [service...]     # docker compose up -d
stack down [service...]   # docker compose stop
```

## Memory service (mem0, host :8077)

```powershell
stack mem0 status|start|stop|restart|logs
stack mem0 users
stack mem0 list cline:<repo> --limit 20
stack mem0 export cline:<repo>
stack mem0 delete cline:<repo> --yes
# if chat memory stops mid-session, just: stack mem0 restart
```

## Meshtastic Hermes memory

```powershell
stack meshtastic start|stop|restart|status|logs
stack meshtastic import-md <folder> --dry-run
stack meshtastic import-md <folder>
```

This is separate from the main Mem0 service. It uses `:8078` for the Mem0 API,
`:8079/mcp` for Hermes, Qdrant collection `mem0_meshtastic`, and profile notes in
`hermes/profiles/meshtastic`.

## Web dashboard (host :8090)

```powershell
stack web start|stop|restart|status     # start opens a browser unless --no-open
```

## Kimi (lazy, on demand)

```powershell
stack kimi proxy-start        # start tiny wake proxy on :8095, no model load
stack kimi status             # proxy/backend state and idle timer; backend is normally down at idle
stack kimi start              # force-load backend on :8096 now (~5 min, ~305GB RAM)
stack kimi stop               # stop backend and free RAM; proxy can stay up
stack kimi logs               # tail backend log
```

OpenWebUI and Hermes should use the LiteLLM model `kimi/kimi-k2.7-code`.
If Kimi is idle, the first request returns a warming error and starts the
backend. Retry after about 400 seconds, once the model has loaded into memory.
Once warm, Kimi stays loaded until 90 minutes pass with no requests.

## Document pipeline (PDFs -> OpenWebUI Knowledge)

```powershell
stack profile extract-gpu
stack mineru bulk <folder> --recursive            # PDFs -> markdown (vlm-auto-engine)
stack skillsusa generate-cards                    # (SkillsUSA) markdown -> retrieval cards
$env:OPENWEBUI_API_TOKEN="..."                    # for the next two
stack openwebui import-knowledge <folder> --knowledge "<name>"   # upload + attach
stack skillsusa smoke                             # (SkillsUSA) retrieval regression test
# then reindex in OpenWebUI admin (or POST /api/v1/knowledge/reindex)
```

## Coding agents (local models)

```powershell
stack profile code            # loads qwen3.6-35b-a3b @ 327680 for Cline / claude-local
                              # also keeps Mem0 working and loads qwen2.5-3b for Hermes sub-agents
                              # qwen uses parallel=1 so one coding agent gets the full window
stack code doctor             # pass/fail check before blaming Cline or the model
stack code smoke              # one tiny LiteLLM request; add --memory-write to test Mem0 writes too
stack code init <repo> --project <name>   # drop .clinerules + local stack metadata into a repo
stack model load <id> --ctx 327680 --parallel 1   # manual: full ctx to one request
```

```powershell
# Cline (VS Code) - the practical local agent. Settings -> API Provider "OpenAI Compatible":
#   Base URL http://localhost:4000/v1 (LiteLLM, metered)  or  http://localhost:1234/v1 (LM Studio)
#   Key = LITELLM_KEY_CLINE (litellm) / OPENAI_API_KEY (lm studio); Model = cline
#   Full setup: cline/cline-setup.md   (qwen3.6-35b-a3b is best at tool-use)

claude-local                  # Claude Code -> LiteLLM -> local model (root claude-local.cmd)
claude-local -p "a prompt"    # one-shot; works but local models are weak at CC's tool-use

# Qwen Code CLI - best-fit terminal agent (native tool format, telemetry off). Install once:
#   npm install -g @qwen-code/qwen-code
qwen-local                    # Qwen Code -> LiteLLM (qwen-code alias) -> qwen (root qwen-local.cmd)
qwen-local -p "a prompt"      # one-shot; telemetry disabled in %USERPROFILE%\.qwen\settings.json
```

## Hermes Agent (Nous autonomous agent, separate clone in WSL)

```bash
# runs in WSL at /opt/hermes-agent; model via LiteLLM, web search via SearXNG
wsl -d Ubuntu-24.04 -u root
cd /opt/hermes-agent
docker compose up -d gateway              # start (auto-starts with Docker too)
docker compose restart gateway            # after editing /root/.hermes/config.yaml
docker exec -it hermes hermes chat        # interactive
docker exec hermes hermes -z "a prompt" --yolo   # one-shot
docker exec hermes hermes status | grep -E 'Model|Provider'
docker compose up -d dashboard            # optional UI on 127.0.0.1:9119
# Full setup + the CRLF/WSL-clone gotcha: hermes/hermes-setup.md
```

## Token usage

- Live locally: the **token usage** card on the dashboard (http://localhost:8090).
- Grafana: reads InfluxDB bucket `GEORGEai`, measurement `llm_usage`
  (tags `user`/`app`/`model`/`status`; fields `prompt_tokens`/`completion_tokens`/
  `total_tokens`/`requests`/`latency_s`). Sample Flux in the README.

## LAN exposure (Administrator / UAC)

```powershell
stack expose                 # portproxy + firewall for 3000/7860/8000 (UAC prompt)
.\scripts\expose-web.ps1     # firewall for the dashboard :8090 (host service)
```
Reach exposed services from the LAN at `http://<HOST_LAN_IP>:<port>`.

## Backups & disaster recovery

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup-stack.ps1   # verified data bundle
scripts\backup-openwebui.cmd                                        # quick OpenWebUI DB snapshot
powershell -ExecutionPolicy Bypass -File scripts\register-tasks.ps1 # (re)create logon tasks
```
Full restore runbook: [RECOVERY.md](RECOVERY.md). Push the repo to a remote to get it off-drive.

## OpenWebUI theming / branding

Edit `openwebui/branding/custom.css` (or re-run `generate_assets.py` for the logo), then:

```powershell
wsl -d Ubuntu-24.04 -u root -- bash -lc "cd /mnt/c/Users/<you>/Documents/local-ai-stack && docker compose up -d --force-recreate open-webui"
```
> OpenWebUI **caches /static in memory**: editing the file isn't enough, you MUST
> force-recreate the container, then hard-refresh the browser (Ctrl+Shift+R).

## Gotchas worth remembering

- `stack shutdown` before any poweroff (data flush).
- OpenWebUI static (CSS/logo) changes need a **container force-recreate**, not just a file edit.
- Inspecting these UTF-8 files on PowerShell 5.1 needs `-Encoding utf8`.
- Container data lives on WSL ext4 (`/srv/local-ai-stack/...`), never `/mnt/c`.
- The dashboard is localhost+LAN with basic auth; never LAN-expose Qdrant/LiteLLM (no auth).
