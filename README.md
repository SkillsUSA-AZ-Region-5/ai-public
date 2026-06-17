# giva

GCTF's local AI stack for generating vulnerable random VMs on the fly, handling
complex coding tasks, and supporting SkillsUSA needs across other competitions.
It runs on a Windows box with NVIDIA GPUs, with inference, chat, agents, memory,
web search, document extraction, and image generation kept local.

## Documentation

New here? Skim this README for the mental model, then run through
[INSTALL.md](INSTALL.md) to build it and keep [CHEATSHEET.md](CHEATSHEET.md) open for
day-to-day commands. The rest is reference.

| Doc | When to read it |
|---|---|
| **Setup & operations** | |
| [INSTALL.md](INSTALL.md) | Building the stack for the first time, or reinstalling from scratch |
| [CHEATSHEET.md](CHEATSHEET.md) | You want the one-liner for a task (start/stop, profiles, health, backups) |
| [NETWORKING.md](NETWORKING.md) | Ports, exposing a service to the LAN, firewall rules |
| [RECOVERY.md](RECOVERY.md) | Setting up backups, or rebuilding after a drive failure |
| **Components** | |
| [cline/cline-setup.md](cline/cline-setup.md) | Wiring a coding agent to local models (Cline, Qwen Code CLI, Claude Code) |
| [hermes/hermes-setup.md](hermes/hermes-setup.md) | Running the Hermes autonomous agent (LiteLLM + SearXNG, Discord bot) |
| [comfyui/COMFYUI.md](comfyui/COMFYUI.md) | Local image generation: ComfyUI + Flux.1-dev, wired into OpenWebUI |
| [skills-usa-readme.md](skills-usa-readme.md) | Turning SkillsUSA PDFs into OpenWebUI Knowledge collections |
| [mineru/BULK.md](mineru/BULK.md) | MinerU bulk PDF-to-markdown extraction |

### On this page

- [Where everything lives](#where-everything-lives)
- [Architecture](#architecture)
- [Daily driving: the `stack` CLI](#daily-driving-the-stack-cli)
- [Powering off the server safely](#powering-off-the-server-safely)
- [Recovery (when something crashes mid-session)](#recovery-when-something-crashes-mid-session)
- [How memory works](#how-memory-works)
- [Document pipeline (PDFs into OpenWebUI Knowledge)](#document-pipeline-pdfs-into-openwebui-knowledge)
- [Coding agents (Cline / Claude Code) on local models](#coding-agents-cline--claude-code-on-local-models)
- [Usage metering](#usage-metering-per-app-and-per-user-token-usage)
- [Repo layout](#repo-layout)
- [Operating notes](#operating-notes)

## Where everything lives

| What | URL | Login |
|---|---|---|
| OpenWebUI (chat) | http://localhost:3000 | your OpenWebUI account |
| MinerU drag-and-drop UI | http://localhost:7860 | `MINERU_AUTH_USER` / `MINERU_AUTH_PASS` from `.env` |
| MinerU REST API | http://localhost:8000/docs | same basic auth |
| LM Studio API | http://localhost:1234/v1 | `OPENAI_API_KEY` from `.env` |
| LM Studio scheduler | http://localhost:1235/v1 | `OPENAI_API_KEY` from `.env` |
| Kimi lazy proxy (on demand) | http://localhost:8095/v1 | none (host process, OpenAI-compatible) |
| LiteLLM gateway | http://localhost:4000/v1 | per-app `LITELLM_KEY_*` from `.env`; master key for admin only |
| Qdrant dashboard | http://localhost:6333/dashboard | none (keep off the LAN) |
| Mem0 health check | http://localhost:8077/health | none (other routes need the Bearer token) |
| Hermes dashboard | http://localhost:9119 | none (127.0.0.1 only; tunnel for remote, see [hermes/hermes-setup.md](hermes/hermes-setup.md)) |
| ComfyUI (image gen) | http://localhost:8188 | none (127.0.0.1 only; only up under `stack profile image`, see [comfyui/COMFYUI.md](comfyui/COMFYUI.md)) |

From another machine on the LAN, swap `localhost` for the box's IP
(`HOST_LAN_IP` in `.env`) for the ports you've exposed with `stack expose`:
OpenWebUI and the two MinerU ports. MinerU only runs while an extract profile
is active (`stack profile extract` or `extract-gpu`), and vLLM takes over
port 8000 when its profile is up.

## Architecture

```
                       WINDOWS HOST

  LM Studio :1234 <----- LM Studio scheduler :1235
       ^                 wakes chat/code profiles, then forwards
       |
       |                 Kimi lazy proxy :8095 ---> Kimi backend :8096
       |                       ^                     CPU+RAM, on demand
       |                       |
       +-----------------------+-------------------------------+
                                                               |
                       WSL2 Ubuntu + docker-ce                 |
                                                               |
  OpenWebUI :3000 ---> LiteLLM :4000 --------------------------+
       |                |   |   |
       |                |   |   +--> litellm-db (virtual keys, spend)
       |                |   +------> Qdrant :6333 (memory vectors)
       |                +----------> SearXNG :8081 (web search)
       |
       +--> Jupyter (code interpreter)
       +--> ComfyUI :8188 (image profile)

  Mem0 service :8077 ---> LiteLLM brain/embed aliases ---> LM Studio
       |
       +--> Qdrant :6333

  Hermes Agent (host-net)
       +--> LiteLLM :4000 (model aliases: hermes, hermes-subagent)
       +--> SearXNG :8081 (search)

  MinerU :7860/:8000 ---> PDF/doc extraction behind Caddy basic auth
```

| Component | Where | Port | What it does |
|---|---|---|---|
| LM Studio | host | 1234 | Serves GGUF models (chat + embeddings) over an OpenAI-compatible API. The `lms` CLI manages loads |
| LM Studio scheduler | host | 1235 | Lazy proxy for chat/code LiteLLM aliases. First request warms `stack profile chat` or `stack profile code`; LM Studio `--ttl` unloads the main model after idle time |
| OpenWebUI | docker | 3000 | Browser chat UI with accounts, web search, RAG and a code interpreter |
| Mem0 service | host (venv) | 8077 | Cross-project agent memory. A brain LLM decides what to store, nomic embeds it, Qdrant keeps the vectors. Used by the OpenWebUI filter and the Cline MCP server |
| LiteLLM | docker | 4000 | Gateway that maps model names to LM Studio (the `gemma`/`qwen` user models, the `brain`/`embed` Mem0 pair, and per-app aliases). Admin UI + virtual keys at `:4000/ui` |
| litellm-db | docker | internal | Postgres backing LiteLLM's virtual keys and spend tracking (no host port) |
| Qdrant | docker | 6333 | Vector store for Mem0 |
| SearXNG | docker | internal | Metasearch backend for OpenWebUI web search (no host port) |
| Jupyter | docker | internal | Sandbox for OpenWebUI's code interpreter (no host port) |
| MinerU | docker | 7860 / 8000 | Turns PDFs and complex documents into markdown (Gradio UI + REST API), behind Caddy basic auth |
| vLLM | docker (off) | 8000 | Optional high-throughput inference, compose profile `vllm` |
| Kimi lazy proxy / backend | host | 8095 / 8096 | Lightweight proxy stays up on 8095. The standalone llama.cpp backend loads Kimi-K2.7-Code GGUF on 8096 only after a request arrives, then unloads after 90 minutes without requests. CPU+RAM only (~3.3 tok/s after load). Reached through the `kimi/kimi-k2.7-code` LiteLLM alias |
| ComfyUI | docker (off) | 8188 | Local image generation (Flux.1-dev fp8), built for Blackwell. Compose profile `image`; OpenWebUI generates through it. See [comfyui/COMFYUI.md](comfyui/COMFYUI.md) |
| Hermes Agent | docker | host-net (9119 dashboard) | Nous Research autonomous agent. Runs through LiteLLM (the `hermes` alias -> qwen) and web-searches through SearXNG. Separate clone at `/opt/hermes-agent` in WSL, see [hermes/hermes-setup.md](hermes/hermes-setup.md) |
| stackctl | host (venv) | n/a | The `stack` CLI that manages all of the above |

## Daily driving: the `stack` CLI

`stack` works from any directory in CMD or PowerShell. [stack.cmd](stack.cmd) is on
PATH, and the PowerShell profiles define a matching `stack` function.

```powershell
stack status                  # neofetch-style overview: CPU/RAM/GPU, models, containers, services
stack doctor [--fix]          # health-check every service; --fix recovers downed ones
stack monitor --start         # background debug snapshots to manage/stack-monitor.log
stack monitor --status|--stop # check or stop the background monitor
stack profiles                # list workload profiles
stack profile chat            # daily driver: gemma(32k) + nomic on GPU, web stack up
stack profile code            # coding agents: qwen3.6-35b-a3b @ 327680 + memory core + Hermes sub-agent model
stack lm-scheduler status     # chat/code profile scheduler state and idle timers
stack code doctor             # coding path check: LM Studio, LiteLLM, keys, aliases, Mem0, dashboard
stack code status             # loaded model + safe aliases for the active profile
stack code smoke              # tiny LiteLLM request; add --memory-write to test Mem0 writes too
stack code init <repo>        # writes .clinerules + .local-ai-stack.json for a project
stack profile image           # image generation: ComfyUI + Flux.1-dev fp8 (Flux on GPU1 + gemma-4-12b chat)
stack profile extract         # adds MinerU on CPU, coexists with chat
stack profile extract-gpu     # MinerU on GPU (unloads the chat model first)
stack profile vllm            # free the GPU for vLLM serving

stack kimi proxy-start        # idle mode: start the tiny Kimi wake proxy on :8095 without loading the model
stack kimi start              # force-load Kimi now: backend on :8096, CPU+RAM, ~5 min load
stack kimi status|stop|logs   # check idle timer / stop / tail the Kimi backend

stack model ps                # loaded LM Studio models
stack model load <id> --ctx 32768
stack model unload            # unload all

stack up / stack down         # main compose stack
stack shutdown [--wsl]        # gracefully stop ALL apps before powering off (flushes data)
stack startup                 # bring the daily stack back up after a shutdown
stack mem0 status|start|stop|restart|logs
stack mem0 users              # list memory user/project ids in Qdrant
stack mem0 list cline:<repo>  # show recent project memories
stack mem0 export cline:<repo>
stack mem0 delete cline:<repo> --yes
stack web start|stop|status   # browser dashboard for all of the above (host, :8090)
stack expose                  # LAN-expose 3000/7860/8000 (UAC prompt, see NETWORKING.md)

# document pipeline: PDFs -> markdown -> OpenWebUI Knowledge
stack mineru bulk <folder>            # PDFs -> markdown (vlm-auto-engine, downloads results)
stack openwebui import-knowledge <folder> --knowledge "<name>"   # upload + attach md to a collection
stack skillsusa generate-cards       # SkillsUSA: markdown -> retrieval helper cards
stack skillsusa smoke                # SkillsUSA: retrieval regression test (read-only)
```

`stack monitor` is a local flight recorder for long debug runs. By default it writes
a full snapshot every 5 minutes and a quick `nvidia-smi` sample every 60 seconds.
It records GPU state, RAM/CPU, LM Studio loaded models, container status, key
health endpoints, watched host processes, and recent error-looking log lines. It
does not start models, send prompts, or recover services. API-key command-line
arguments are redacted in the log.

The GPUs hold about 32 GB between them (2x RTX 5060 Ti 16GB). A profile is just "which models are on the
GPU and which containers run", and applying one does the VRAM juggling for you.
The combo that has proven itself on this box is gemma-4-26b-a4b-qat (15.6 GB MoE,
roughly 28 tok/s) as the main chat model, plus qwen2.5-1.5b-instruct as the small
Mem0 brain and nomic-embed for embeddings. The code profile also keeps
qwen2.5-3b-instruct loaded CPU-only for Hermes sub-agents.
Models that don't fit in VRAM spill to CPU and crawl, so verify memory headroom
before trying anything bigger.

## Powering off the server safely

Don't just hit Windows shutdown. That kills the WSL VM abruptly and containers may not
flush (Qdrant vectors, OpenWebUI's SQLite). Instead:

```powershell
stack shutdown        # stops host services first (mem0 -> Qdrant), then containers with a
                      # 30s grace so they flush. Then power off Windows normally.
stack shutdown --wsl  # same, and also shuts the WSL VM down (if you're not powering off)
```

Bring it back after boot (containers stay stopped once `shutdown` marks them):

```powershell
stack startup         # core containers + mem0 + web dashboard
stack profile chat    # then load the models (LM Studio must be running)
```

A plain reboot *without* `stack shutdown` is mostly self-healing (containers have
`restart: unless-stopped`, host services auto-start at logon), but you risk the ungraceful
container stop. Use `stack shutdown` whenever you can.

## Recovery (when something crashes mid-session)

The host services auto-start at logon (scheduled tasks), but if one dies while you're
working, recover it without restarting anything else:

```powershell
stack doctor                  # what's up / down across host services + containers
stack doctor --fix            # restart downed host services + bring up missing containers
stack mem0 restart            # just the memory service (e.g. chat memory stopped working)
stack web restart             # just the dashboard
stack up                      # (re)start the core containers
```

`stack doctor --fix` is the catch-all: it restarts mem0/web if down and runs
`docker compose up -d` for any missing container. LM Studio is a desktop app, so if it's
down `doctor` tells you to start it by hand. If memory stops working mid-chat, it's almost
always the Mem0 service. `stack mem0 restart` and keep going; OpenWebUI itself is unaffected.

## How memory works

1. Anything that wants memory (the OpenWebUI filter, Cline via MCP) POSTs text to
   the Mem0 service. `/record` is fire-and-forget.
2. The **brain model** decides what's worth keeping and rewrites it as facts; nomic
   embeds them and Qdrant stores the vectors.
3. `/recall` is a plain vector search, about 0.1 s, no LLM in the loop.
4. Memory is partitioned by `user_id`: one per OpenWebUI account, one per Cline
   project (`cline:<project>`), plus a shared `cline:global` pool.

**The brain is a small dedicated model** (`qwen2.5-1.5b-instruct`, ~1.65 GB), *not* the chat
model. It plus nomic form the "memory core" that every profile keeps resident, so you
can unload/swap the **main** chat model (qwen, a bigger gemma, etc.) and memory still
records and recalls. It's tiny and non-reasoning on purpose: it fits on the GPU even
alongside qwen-35b in the `code` profile (the old 6.3 GB gemma-4-e4b brain didn't, so memory
silently failed there), and it emits clean JSON with no thinking tokens. LiteLLM's `brain`
alias points at it ([litellm/config.yaml](litellm/config.yaml)); the memory core is in
`MEMORY_CORE` in [manage/stackctl.py](manage/stackctl.py). To swap the main model:
`stack model load <id>` then `stack model unload <old>`, but leave `qwen2.5-1.5b-instruct`
and nomic loaded.

## Document pipeline (PDFs into OpenWebUI Knowledge)

Turning a folder of PDFs into searchable Knowledge is a few explicit stages, not one
command, so you can inspect each step before pushing to the live collection:

1. `stack profile extract-gpu` then `stack mineru bulk <folder>`: MinerU extracts each
   PDF to markdown (downloads the results to an `output/` folder, resumable).
2. *(optional, SkillsUSA-specific)* `stack skillsusa generate-cards`: builds compact
   retrieval "helper cards" that disambiguate confusable contests.
3. `stack openwebui import-knowledge <folder> --knowledge "<name>"`: the upload step.
   It auto-creates (or reuses) the collection and group, uploads + attaches each `.md`,
   embeds it, and tracks progress in a manifest so re-runs skip finished files.
4. Reindex: OpenWebUI admin tools, or `POST /api/v1/knowledge/reindex`. (Import does
   *not* reindex for you.)
5. `stack skillsusa smoke`: read-only retrieval regression test; both case tiers
   should be all-PASS.

The full add/remove/rebuild lifecycle, the helper-card rationale, and the smoke suite
live in [skills-usa-readme.md](skills-usa-readme.md). The `mineru bulk` and
`import-knowledge` commands are generic; only `generate-cards`/`smoke` are
SkillsUSA-specific.

## Coding agents (Cline / Claude Code) on local models

- **Cline** (VS Code) is the practical local coding agent. Point it at LiteLLM (`:4000/v1`,
  metered) or LM Studio (`:1234/v1`). Setup + recommended models in
  [cline/cline-setup.md](cline/cline-setup.md). qwen3.6-35b-a3b is best at tool-use.
- **Claude Code** can also drive local models: LiteLLM exposes the Anthropic `/v1/messages`
  format, so [claude-local.cmd](claude-local.cmd) launches `claude` against the gateway
  (`claude-local-*` aliases in [litellm/config.yaml](litellm/config.yaml)). It *works*, but
  Claude Code is tuned for Claude-grade tool-use, so local models make agentic coding rough.
  Cline is the better fit. Either way the decoupled Mem0 brain means memory keeps working.
- **qwen in OpenWebUI chat:** OpenWebUI lists models by their LiteLLM name. qwen is exposed as
  `qwen/qwen3.6-35b-a3b`, so you can chat with it in OpenWebUI, but only under `stack profile code`
  (that is when qwen is loaded). Picking it in `chat`/`image` mode would JIT-load 22GB of qwen on
  top of the loaded model. In `chat` mode use `google/gemma-4-26b-a4b-qat`; in `image` mode use
  `google/gemma-4-12b-qat`. The model you pick must match the loaded profile.

## Usage metering (per-app and per-user token usage)

Every client (OpenWebUI, Cline, the CLIs, Hermes) goes **through LiteLLM**, not straight to
LM Studio, so token usage can be metered. A LiteLLM success callback
([litellm/usage_callback.py](litellm/usage_callback.py)) writes one point per completion to
your **external InfluxDB v2** (graph it in your own Grafana) *and* to the local stackctl
dashboard.

- **Schema:** measurement `llm_usage`, tags `user` / `app` / `model` / `status`, fields
  `prompt_tokens`, `completion_tokens`, `total_tokens`, `requests`, `latency_s`.
- **`app` = which client made the call.** The `model` tag records the *resolved* model, so all
  the qwen clients look identical there. To tell them apart, the callback attributes by the
  **requested LiteLLM alias** instead, so give each app its own (`hermes`, `cline`, `qwen-code`,
  `claude-local-*`) in [litellm/config.yaml](litellm/config.yaml). OpenWebUI is detected by its
  forwarded headers. See the alias notes in the component setup docs.
- **`user` = the person** (OpenWebUI accounts, via `X-OpenWebUI-User-*` headers with
  `ENABLE_FORWARD_USER_INFO_HEADERS=true`). Falls back to `anon` for headless clients.
- **Configure** InfluxDB in `.env` (`INFLUX_URL` / `INFLUX_ORG` / `INFLUX_BUCKET` /
  `INFLUX_TOKEN`). The Influx sink **no-ops** if these are unset, so it's opt-in.
- **Per-app virtual keys, via Postgres.** The metering above is *attribution* (labels).
  LiteLLM's Postgres container (`litellm-db`) lets the running apps use their own keys from `.env`
  instead of the master key, so spend/usage can be grouped per app:
  ```bash
  curl -s -X POST http://localhost:4000/key/generate -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    -H 'Content-Type: application/json' \
    -d '{"key_alias":"cline"}'
  ```
  Or use the **admin UI at http://localhost:4000/ui** (login `UI_USERNAME` / `UI_PASSWORD` from
  `.env`) to create keys and watch spend. The current app keys are tracking-only: no spend caps or model allow-lists;
  keep the master key for administration. Models stay in `config.yaml` (`STORE_MODEL_IN_DB=False`).
- **Rollback:** point `OPENAI_API_BASE_URL` back to `http://${HOST_LAN_IP}:1234/v1` and
  `OPENAI_API_KEY=${OPENAI_API_KEY}` in [docker-compose.yml](docker-compose.yml). To drop the DB
  entirely, remove the `litellm-db` service and LiteLLM's `DATABASE_URL`; metering still works.

The **local** view needs no InfluxDB. The callback also posts each event to the stackctl
dashboard (`/api/usage/event`, shared `USAGE_INGEST_TOKEN`), which stores it in
`manage/usage.db` (SQLite) and shows a **token usage** card at http://localhost:8090 with
totals plus by app, by user, and by model over the last 7 days.

## Repo layout

```
docker-compose.yml      main stack (openwebui, litellm, qdrant, searxng, jupyter, vllm)
.env                    machine config + secrets (gitignored), template: .env.example
manage/stackctl.py      the `stack` CLI (core functions shared with the web dashboard)
manage/webapp.py        the web dashboard (host FastAPI on :8090, reuses stackctl)
memori/                 Mem0 service + MCP server + venv (mem0_service.py, mcp_server.py)
mineru/                 MinerU images + composes (CPU and GPU) + Caddy auth proxy + bulk_mineru.py
openwebui/              plugins (memory filter, web-fetch) + Knowledge import/remove/smoke scripts
skillsusa/              SkillsUSA ingestion scripts, smoke cases, and local generated data (ignored)
litellm/config.yaml     gateway model aliases (incl. claude-local-*); usage_callback.py -> InfluxDB + dashboard
searxng/settings.yml    search engine config (JSON output enabled for OpenWebUI)
cline/                  Cline setup (cline-setup.md), MCP snippet, custom instructions
claude-local.cmd        launch Claude Code against the local models via LiteLLM
scripts/                installers, expose, backups, keepalive, service launchers
backups/                legacy OpenWebUI DB snapshots; DR uses scripts/backup-stack.ps1
```

## Operating notes

- Docker means docker-ce inside WSL2, not Docker Desktop. WSL networking stays on
  NAT, because mirrored mode broke port-forwarding. Containers reach the host
  through its LAN IP, which is set once as `HOST_LAN_IP` in `.env`. The repo itself
  is path-portable: scripts find themselves, and `WSL_DISTRO` is configurable too.
- Container data lives on WSL ext4 (`/srv/local-ai-stack/...`), never on `/mnt/c`,
  because SQLite WAL breaks on the 9p filesystem.
- The WSL VM idles out and takes everything down with it. The `WSL-KeepAlive`
  scheduled task prevents that.
- Exposing a port to the LAN takes a portproxy bound to the specific LAN IP
  (binding 0.0.0.0 self-loops and kills localhost too) plus a firewall rule.
  `stack expose` does both.
- The stackctl dashboard is windowless; its helper commands (`wsl`, `docker`, `lms`,
  `nvidia-smi`) are hidden too, or RDP gets console-window flashes.
- Mem0's embedder config must not set `embedding_dims`, and the brain model needs
  `response_format` dropped. Both handled in litellm/config.yaml.
- The venv `pythonw.exe` is a launcher stub, so one Mem0 service shows up as two
  processes (parent stub plus child interpreter). That's normal, not a duplicate.
