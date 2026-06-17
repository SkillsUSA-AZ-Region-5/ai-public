# Hermes Agent on the self-hosted stack

[Hermes Agent](https://hermes-agent.nousresearch.com/hermes-agent) is Nous Research's
autonomous agent. Here it runs entirely on local infrastructure: the model comes from
LM Studio through the LiteLLM gateway, and web search goes through the local SearXNG.
No cloud provider keys are configured, so the agent cannot phone home even if asked to.

Unlike the rest of the stack, Hermes is **not** part of `docker-compose.yml`. It builds
from its own source tree, so it lives in a separate clone at `/opt/hermes-agent` inside
WSL and has its own `docker compose`.

| Thing | Value |
|---|---|
| Source clone | `/opt/hermes-agent` (WSL ext4, **not** `/mnt/c`) |
| Pinned commit | `484f484` (2026-06-11) |
| Image | `hermes-agent:latest` (~4.9 GB) |
| Container | `hermes` (gateway), optional `hermes-dashboard` |
| Networking | `network_mode: host` (shares the WSL host loopback) |
| Config + state | `/root/.hermes/` (mounted to `/opt/data` in the container) |
| Model | LiteLLM `hermes` alias to qwen3.6-35b-a3b (the `stack profile code` model) at `http://127.0.0.1:4000/v1` |
| Delegation model | LiteLLM `hermes-subagent` alias to qwen2.5-3b-instruct, loaded CPU-only by `stack profile code` |
| Web search | SearXNG at `http://127.0.0.1:8081` |
| Dashboard | http://localhost:9119 (127.0.0.1 only) |

### On this page

- [Why it must be cloned inside WSL](#why-it-must-be-cloned-inside-wsl-the-one-gotcha-that-will-bite-you) (the s6/CRLF gotcha)
- [Recreate from scratch](#recreate-from-scratch) (clone, build, first start)
- [Point it at the stack](#point-it-at-the-stack) (model via the `hermes` alias, SearXNG config)
- [Verify](#verify) (prove it's running locally end-to-end)
- [Day to day](#day-to-day) (start/stop/logs/rebuild)
- [Connect to Discord](#connect-to-discord) (the bot, the `env_file` gotcha, invites)
- [Notes](#notes) (secrets, other messaging gateways, dashboard auth)

## Why it must be cloned inside WSL (the one gotcha that will bite you)

Hermes uses [s6-overlay](https://github.com/just-containers/s6-overlay) as its init
system. s6 reads tiny service-definition files like
`docker/s6-rc.d/dashboard/type` whose entire contents are one word (`longrun`,
`oneshot`, or `bundle`). If those files have Windows CRLF line endings, the word becomes
`longrun\r` and the container dies at boot with:

```
s6-rc-compile: fatal: invalid /etc/s6-overlay/s6-rc.d/dashboard/type: must be oneshot, longrun, or bundle
```

A clone on the Windows filesystem (or `git clone` run from Windows with
`core.autocrlf=true`) converts those files to CRLF and breaks the build. The repo's
`.gitattributes` only forces LF on `*.sh`, not on the extensionless s6 `type` files.

**Fix: clone inside WSL**, where git checks out LF by default. That is the whole reason
the source lives at `/opt/hermes-agent` and not under `Documents`.

## Recreate from scratch

```bash
# 1. Clone INSIDE WSL (ext4 -> LF line endings). Do not clone on /mnt/c.
wsl -d Ubuntu-24.04 -u root
rm -rf /opt/hermes-agent
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent

# sanity check: this must print "longrun" with no ^M
cat -A /opt/hermes-agent/docker/s6-rc.d/dashboard/type    # -> longrun$   (good)
                                                          # -> longrun^M$ (CRLF, will break)

# 2. Build. The apt/pip/npm/playwright layers are heavy (~15-20 min cold).
#    Run it detached so it survives the shell closing.
cd /opt/hermes-agent
systemd-run --unit=hermes-build --collect bash -lc \
  'cd /opt/hermes-agent && docker compose build > /tmp/hermes-build.log 2>&1'
# watch:  systemctl is-active hermes-build ; tail -f /tmp/hermes-build.log

# 3. Start the gateway. UID/GID default to 10000 and match /root/.hermes ownership.
docker compose up -d gateway
# On first run it seeds /root/.hermes/config.yaml with cloud defaults; we override below.
```

## Point it at the stack

Two edits to `/root/.hermes/`. Both are already applied on this box; this is what to
reapply after a fresh `hermes setup` or a config reset.

### 1. Model -> LiteLLM (`/root/.hermes/config.yaml`)

The `provider: custom` form is trusted only when `base_url` is loopback, which it is
here, so no separate `custom_providers` entry is needed.

```yaml
model:
  default: hermes                       # a LiteLLM ALIAS (-> qwen3.6-35b-a3b, what `stack profile code` loads)
  provider: custom                      # `hermes` mirrors `chat`'s target but is Hermes-specific, so
  base_url: http://127.0.0.1:4000/v1    # the usage dashboard can attribute traffic to Hermes (see below).
  api_key: <LITELLM_KEY_HERMES>         # per-app LiteLLM virtual key for tracking Hermes usage
```

**Use an alias, not a raw model id.** Pinning a specific id like
`google/gemma-4-26b-a4b-qat` is a trap: if that model isn't the one currently loaded, LiteLLM
makes LM Studio **JIT-load it** at its default **8k context**, which then (a) fights the loaded
model for VRAM and (b) overflows on Hermes's long prompts (`request exceeds available context
size (8192)`). Use the **`hermes`** alias: it targets the same model as `chat`
(qwen under `stack profile code`) but is a **dedicated alias so the usage dashboard tags Hermes's
traffic as `app=hermes`** instead of lumping it into the shared `chat`/`anon` bucket (the dashboard's
`model` tag carries the *resolved* model, so every qwen client collapses into one bar; attribution
is by the requested alias). The Mem0 brain is decoupled, so this doesn't touch memory. (If you switch
to a profile that unloads qwen, repoint the `hermes` alias in `litellm/config.yaml` or Hermes will
JIT-load qwen at 8k.)

### 2. Delegation -> LiteLLM (`/root/.hermes/config.yaml`)

Hermes sub-agents use a dedicated small CPU model instead of the main coding model. This keeps
delegated work from queueing behind qwen3.6-35b-a3b, which is loaded with `parallel=1` so the
main coding agent gets the full context window.

```yaml
delegation:
  model: hermes-subagent
  provider: custom
  base_url: http://127.0.0.1:4000/v1
  api_key: <LITELLM_KEY_HERMES>
  max_concurrent_children: 16
  max_spawn_depth: 3
  orchestrator_enabled: true
  inherit_mcp_toolsets: true
```

`hermes-subagent` points to `qwen2.5-3b-instruct` in [../litellm/config.yaml](../litellm/config.yaml).
`stack profile code` loads it CPU-only with `--context-length 65536 --parallel 16`, so the
delegation path survives profile reloads and LM Studio restarts. Do not raise
`max_concurrent_children` above 16 unless the model is loaded with more parallel slots.

### 3. Web search -> SearXNG (`/root/.hermes/config.yaml` + `/root/.hermes/.env`)

```yaml
web:
  backend: searxng
  search_backend: searxng
  extract_backend: ''     # SearXNG searches but does not fetch/extract pages
```

```ini
# /root/.hermes/.env
SEARXNG_URL=http://127.0.0.1:8081     # SearXNG, published on the host loopback by the stack
GATEWAY_ALLOW_ALL_USERS=true          # local single-user box; otherwise every user is denied
```

Then restart so it reloads config and env:

```bash
cd /opt/hermes-agent && docker compose restart gateway
```

## Verify

```bash
# model + provider resolved
docker exec hermes hermes status | grep -E 'Model|Provider'
#   Model:    hermes
#   Provider: Custom endpoint

# upstreams reachable from inside the container
docker exec hermes curl -s http://127.0.0.1:4000/v1/models | head -c 200   # LiteLLM
docker exec hermes curl -s 'http://127.0.0.1:8081/search?q=test&format=json' | head -c 80   # SearXNG

# sub-agent model through LiteLLM
curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_KEY_HERMES" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-subagent","messages":[{"role":"user","content":"Reply with exactly: SUBAGENT_OK"}],"max_tokens":20}'

# end-to-end generation (proves the request reaches LiteLLM)
docker exec hermes hermes -z 'Reply with exactly: HERMES_LOCAL_OK' --yolo
# then confirm it landed:
docker logs --tail 20 litellm | grep 'chat/completions'   # -> POST /v1/chat/completions 200 OK

# end-to-end web search
docker exec hermes hermes -z 'Search the web for the official SkillsUSA website and give me the URL.' --yolo
```

A reply on its own proves nothing (a cloud model would answer too). What proves it is
local is that no cloud provider is configured (`hermes status` shows them all as "not
logged in") and the `POST /v1/chat/completions 200 OK` line appears in the LiteLLM log.

## Day to day

```bash
cd /opt/hermes-agent
docker compose up -d gateway        # start (also auto-starts with Docker: restart=unless-stopped)
docker compose restart gateway      # after a config change
docker compose stop gateway         # stop
docker logs --tail 40 hermes        # logs
docker exec hermes hermes -z '...'  # one-shot prompt
docker exec -it hermes hermes chat  # interactive

# optional web dashboard on 127.0.0.1:9119
docker compose up -d dashboard
```

The container has `restart: unless-stopped`, so it comes back with Docker on boot
alongside the rest of the stack. Nothing extra to register.

### Rebuild after pulling upstream changes

```bash
cd /opt/hermes-agent
git pull                            # still inside WSL, keeps LF endings
docker compose build
docker compose up -d gateway
```

## Connect to Discord

Hermes has a built-in Discord gateway. The bot then chats through your **local model +
SearXNG search** from Discord (DMs, or `@mention` in a server channel).

1. **Make the bot** at the [Discord Developer Portal](https://discord.com/developers/applications):
   New Application → **Bot** (Public Bot ON) → **enable Message Content Intent AND Server
   Members Intent** (this is the #1 gotcha; without Message Content the bot sees empty
   messages) → Reset Token and copy it.
2. **Invite it** to your server (Installation tab, or the manual URL with
   `scope=bot+applications.commands&permissions=274878286912`).
3. **Get your Discord user ID** (Settings → Advanced → Developer Mode, then right-click your
   name → Copy User ID).
4. **Configure Hermes.** Put the credentials in `/root/.hermes/.env`:
   ```ini
   DISCORD_BOT_TOKEN=<token>
   DISCORD_ALLOWED_USERS=<your user id>     # required, or it denies everyone
   DISCORD_HOME_CHANNEL=<channel id>        # optional: where proactive/cron messages post
   ```

**THE CONTAINER GOTCHA (cost real debugging):** Hermes reads *some* settings straight from
the `.env` file via a config-aware lookup (that's how `SEARXNG_URL` works), but **platform
detection reads the real process environment**, and in the container the file is mounted at
`/opt/data/.env`, not the `~/.hermes/.env` path Hermes auto-loads into `os.environ`. So
`DISCORD_BOT_TOKEN` in the file alone does **nothing** (gateway still logs "No messaging
platforms enabled"). Fix: load that file as a real `env_file` via a local
`docker-compose.override.yml` (compose auto-merges it; upgrade-safe, no secrets in it):

```yaml
# /opt/hermes-agent/docker-compose.override.yml
services:
  gateway:
    env_file:
      - /root/.hermes/.env
```

Then recreate (not just restart, so the override is read):
```bash
cd /opt/hermes-agent && docker compose up -d gateway
```

**Verify:** `grep -E "discord connected|platform" /root/.hermes/logs/gateway.log` should show
`✓ discord connected` and `Gateway running with 1 platform(s)`. The bot shows online; DM it or
`@mention` it. (Runtime status is in `/root/.hermes/logs/gateway.log`, NOT `docker logs`; the
container stdout only shows s6 init + the startup banner.)

- **Home channel `Missing Access` (403 / "0 targets"):** the bot can't see that channel. It
  must be **in that server** and have **View Channel + Send Messages** permission there
  (private channels need the bot's role added). Only affects proactive messages; DM/@mention
  chat is unaffected. Drop `DISCORD_HOME_CHANNEL` if you don't need proactive posts.
- **In server channels** the bot only replies when `@mentioned` (default). Set
  `DISCORD_REQUIRE_MENTION=false`, or list channels in `DISCORD_FREE_RESPONSE_CHANNELS`, to
  change that. Full reference: `/opt/hermes-agent/website/docs/user-guide/messaging/discord.md`.

## Notes

- **Secrets:** `/root/.hermes/config.yaml` (Hermes LiteLLM virtual key) and `/root/.hermes/.env`
  (Discord bot token, etc.) stay on the box and are never committed. The
  `docker-compose.override.yml` above only *references* the .env path, so it carries no
  secrets.
- **Other messaging gateways** (Telegram, Slack, WhatsApp, Teams, Google Chat) work the same
  way: their creds go in `/root/.hermes/.env` (now loaded as real env via the override) per
  the upstream docs under `/opt/hermes-agent/website/docs/`.
- **Dashboard auth:** the dashboard stores API keys and has no auth, so it binds to
  127.0.0.1 only. For remote access tunnel it: `ssh -L 9119:localhost:9119 <box>`. Do
  not bind it to `0.0.0.0`.
