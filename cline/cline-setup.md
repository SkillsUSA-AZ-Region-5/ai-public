# Cline on the self-hosted stack

Cline (VS Code coding agent) is the **practical** local-coding path. It's built for
OpenAI-compatible endpoints and degrades far more gracefully on local models than Claude
Code does. Three pieces: the **API provider** (which model), **memory** (MCP), and
**instructions**.

### On this page

- [1. API provider (the model)](#1-api-provider-the-model) (point Cline at LiteLLM/LM Studio)
- [2. Memory (cross-project, via MCP)](#2-memory-cross-project-via-mcp)
- [3. Instructions](#3-instructions) (the custom-instructions paste)
- [4. Web search (shared SearXNG MCP)](#4-web-search-shared-searxng-mcp)
- [Qwen Code CLI](#qwen-code-cli-the-best-fit-local-coding-agent) (the best-fit terminal coding agent)
- [Aside: Claude Code against local models](#aside-claude-code-against-local-models)

## 1. API provider (the model)

Cline → Settings → **API Provider: "OpenAI Compatible"**. Two options:

**A. Through LiteLLM (recommended, usage gets metered).** Cline's tokens then show up on
the dashboard's usage card and in InfluxDB, just like OpenWebUI:
- **Base URL:** `http://localhost:4000/v1`
- **API Key:** your `LITELLM_KEY_CLINE` (from `.env`)
- **Model ID:** `cline`

**B. Straight to LM Studio (simplest):**
- **Base URL:** `http://localhost:1234/v1`
- **API Key:** your `OPENAI_API_KEY` (the LM Studio key, from `.env`)
- **Model ID:** `google/gemma-4-26b-a4b-qat`

> **Model choice for coding: just run `stack profile code`.** Tool-calling quality matters a lot
> for an agent. `qwen/qwen3.6-35b-a3b` is **markedly better at tool-use** than gemma, which throws
> "Invalid tool parameters" when asked to write whole files. `stack profile code` loads qwen at
> **327680 context with parallel=1** (so the one agent gets the full window) and points the
> `claude-local-*` aliases at it. Set Cline's **Model ID** to **`cline`** (a dedicated LiteLLM alias
> that targets qwen3.6-35b-a3b, so the usage dashboard tags Cline's traffic as `app=cline` instead of the shared
> `anon` bucket; `qwen/qwen3.6-35b-a3b` or `claude-local-main` also work but won't be attributed
> separately). Set Cline's **context window to 327680** so it uses the whole thing.
> Heads-up: LM Studio splits context across `parallel` slots, so if you load qwen yourself, pass
> `--parallel 1` or a single request only sees `ctx / parallel` tokens.
> The Mem0 brain now stays loaded in code mode as `qwen2.5-1.5b-instruct`, and the profile also
> loads `qwen2.5-3b-instruct` CPU-only for Hermes sub-agents. qwen3.6 is a *reasoning* model: it
> "thinks" first, so give it token headroom (too low a max-tokens returns an empty reply because
> the budget was spent thinking).

## 2. Memory (cross-project, via MCP)

Merge `cline/mcp-settings.snippet.json` into Cline's MCP settings at
`%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`.
Set a distinct `MEMORI_PROJECT` per workspace (e.g. the repo name) for per-project memory;
the shared `cline:global` pool is always recalled too. The Mem0 service must be running
(`stack mem0 status`).

## 3. Instructions

Paste `cline/custom-instructions.md` into Cline's Custom Instructions so it auto-calls
`recall_memory` / `record_memory`.

## 4. Web search (shared SearXNG MCP)

Local models don't have native web search, so it's added as an **MCP tool** backed by the
local SearXNG. The same server works for **Cline, Claude Code, and Qwen Code**:

- **Server:** `mcp/searxng_search.py` (exposes a `web_search` tool → queries SearXNG at
  `localhost:8081`, published by docker-compose for host-side MCPs). SearXNG must be up.
- **Cline:** the `searxng` entry is in `cline/mcp-settings.snippet.json` (merge it in).
- **Claude Code:** registered user-scoped with
  `claude mcp add searxng -- <venv-python> mcp/searxng_search.py` (so it works in any
  session, including against local models where the native `WebSearch` tool can't). Check
  with `claude mcp list`.
- **Qwen Code:** its native search is Gemini's Google-grounding tool, which needs Google
  auth we don't use, so add the same MCP instead:
  `qwen mcp add searxng <venv-python> mcp\searxng_search.py` (user scope). Check with
  `qwen mcp list`. In a one-shot, allow it with `--allowed-mcp-server-names searxng`.

(OpenWebUI and Hermes already have their own SearXNG-backed search; this MCP is for the
agents that don't.)

---

## Qwen Code CLI (the best-fit local coding agent)

[Qwen Code](https://github.com/QwenLM/qwen-code) is a terminal coding agent built *for* the
qwen models. Its tool-call format is native to the model, so it largely **sidesteps the
"Invalid tool parameters" failures** that Claude Code hits on local models (where the model
has to emit a whole file as one perfectly-escaped JSON arg). If you're coding against this
stack from a terminal, this is the steadiest option.

**Install** (needs Node; it's a global npm package):
```powershell
npm install -g @qwen-code/qwen-code      # provides the `qwen` command
```

**Launch** with the repo-root **`qwen-local.cmd`** (mirrors `claude-local.cmd`):
```powershell
qwen-local                 # interactive
qwen-local -p "a prompt"   # one-shot
```
It points qwen at `http://localhost:4000/v1` (LiteLLM, so usage is metered), authenticates with
your `LITELLM_KEY_QWEN`, and uses model `qwen-code` (a dedicated alias in `litellm/config.yaml`
→ qwen3.6-35b-a3b, so qwen-cli usage meters separately). Run **`stack profile code`** first so the
model is loaded.

> **Why the launcher uses CLI flags, not env vars** (a gotcha that cost real time): qwen-code
> auto-loads a `.env` from the working directory. This repo's `.env` defines its own
> `OPENAI_API_KEY` (the LM Studio key), which would clobber an env-set LiteLLM key and give you a
> **401**. And mixing flags with `OPENAI_MODEL` makes qwen ignore it and fall back to its DashScope
> default `qwen3.5-plus` (→ **400 invalid model**). So the launcher passes
> `--openai-api-key`, `--openai-base-url`, `--model qwen-code`, and `--auth-type openai` explicitly.
> Flags outrank the `.env`, so it works deterministically from any project directory.

**Telemetry is disabled.** Qwen Code is a Gemini-CLI fork that ships with telemetry/usage
stats on. They're turned off in `%USERPROFILE%\.qwen\settings.json`:
```json
{
  "telemetry": { "enabled": false, "logPrompts": false },
  "privacy":   { "usageStatisticsEnabled": false }
}
```
(qwen rewrites this file on first run but preserves the values; it also adds a `"$version"`
field.) Note: there have been reports of it still attempting a telemetry flush even when
disabled. For a hard guarantee, block its outbound at the firewall or use the community
`qwen-code-no-telemetry` build. All inference traffic itself stays on `localhost:4000`.

**Web search:** qwen-code's built-in search is Gemini's Google-grounding tool (needs Google
auth, which we don't use), so it has **no working native web search** here. Add the shared
SearXNG MCP instead (see §4): `qwen mcp add searxng <venv-python> mcp\searxng_search.py`. Then
qwen has a `web_search` tool backed by local SearXNG, same as Cline/Claude Code.

**Verify it's local:** the request must land at LiteLLM. `qwen-local -p "say OK"` then check
`docker logs --tail 5 litellm` for `qwen-code` + `POST /v1/chat/completions 200 OK`.

---

## Aside: Claude Code against local models

Claude Code can *also* point at this stack. It speaks the Anthropic API and LiteLLM exposes
the matching `/v1/messages` endpoint. Use the **`claude-local.cmd`** launcher at the repo root:

```powershell
claude-local              # interactive, against the local model via LiteLLM
claude-local -p "..."     # one-shot
```

The launcher sets `ANTHROPIC_BASE_URL=http://localhost:4000` plus the auth + model env vars and
runs `claude`. **Run `stack profile code` first** so the model it points at (qwen, strong tool-use)
is actually loaded. Three things that bite:

- **Auth:** it sets **`ANTHROPIC_API_KEY`** (= your `LITELLM_KEY_CLAUDE` virtual key), not just
  `ANTHROPIC_AUTH_TOKEN`, because Claude Code's "logged in" check wants `ANTHROPIC_API_KEY`, or you get
  **"not logged in"**. If you're signed into a Claude subscription, the env key overrides it
  (may prompt once to confirm).
- **Models:** the `claude-local-main` / `claude-local-fast` aliases in `litellm/config.yaml` point
  at `qwen/qwen3.6-35b-a3b` (what `stack profile code` loads). Edit them if you load something else.
- **"Invalid tool parameters":** that's the *model* emitting malformed tool args, not a shell or
  PowerShell problem (Claude Code's Bash tool runs through Git Bash, not PowerShell). gemma does it
  constantly on file-writes; qwen mostly doesn't, which is why the `code` profile exists. For
  heavy file-scaffolding, Cline is still steadier than Claude Code on local models.

**Confirm it's actually local** (a reply alone proves nothing; real Claude would answer too):
the request must land at LiteLLM. Either watch the dashboard's **token usage** card tick up, or
check the gateway log:
```powershell
wsl -d Ubuntu-24.04 -u root -- docker logs --tail 5 litellm   # look for POST /v1/messages
```

Reality check: Claude Code is tuned for Claude-grade tool-use, so local models make it rough.
**Cline (above) is the better local agent.**
