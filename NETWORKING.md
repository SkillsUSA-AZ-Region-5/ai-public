# Networking & firewall, local AI stack

**Host LAN IP:** `<HOST_LAN_IP>` (lives in `.env` as `HOST_LAN_IP`)

## The rule of thumb
- WSL/Docker services bind `0.0.0.0:<port>` inside the WSL VM. WSL NAT forwards
  that to Windows **localhost** automatically, but not to the LAN.
- To reach a WSL service from another machine you need, per port:
  1. a `netsh portproxy` from `<LanIp>:<port>` to `127.0.0.1:<port>`. Listen on the
     specific LAN IP, never `0.0.0.0`, which self-loops and breaks localhost too.
  2. an inbound firewall rule.
  `scripts/expose-services.ps1` does both (run as Administrator, or use `stack expose`).
- Host services (LM Studio, the Mem0 service) bind on the host directly, so they
  only need a firewall rule, no portproxy.

## Ports
| Service | Port | Binds in | localhost | LAN | Auth | Notes |
|---|---|---|---|---|---|---|
| OpenWebUI | 3000 | WSL/Docker | yes | **yes** (portproxy+fw) | login | main chat UI |
| MinerU Gradio | 7860 | WSL/Docker/Caddy | yes | needs `expose` | basic auth | drag-drop PDFs |
| MinerU API | 8000 | WSL/Docker/Caddy | yes | needs `expose` | basic auth | batch extraction; shares 8000 with vLLM (mutually exclusive via `stack` profiles) |
| SearXNG | 8081 | WSL/Docker | yes, bound to 127.0.0.1 | no | none | search backend for OpenWebUI plus host-side MCP clients; never LAN-expose it |
| LiteLLM | 4000 | WSL/Docker | yes | **don't** | master key | internal gateway + admin UI (`/ui`), keep off the LAN |
| litellm-db | none | WSL/Docker internal | via litellm | no | password | Postgres for LiteLLM keys/budgets/spend, no host port |
| Qdrant | 6333 | WSL/Docker | yes | **don't** | none | memory vectors, keep off the LAN |
| Jupyter | none | WSL/Docker | via OpenWebUI | n/a | token | code-interpreter sandbox, no host port |
| vLLM | 8000 | WSL/Docker | when running | optional | api key | only up under `stack profile vllm` |
| ComfyUI | 8188 | WSL/Docker | when running | needs `expose` | none | image gen (Flux); only up under `stack profile image`. No auth, so keep off the LAN unless you add some |
| Mem0 service | 8077 | host | yes | yes (fw, scoped) | Bearer token | firewall rule already added |
| stackctl web | 8090 | host | yes | yes (fw) | basic auth | dashboard; `scripts/expose-web.ps1` opens the fw. Bind `WEB_BIND=0.0.0.0` + `WEB_AUTH_PASS` in `.env` |
| LM Studio | 1234 | host | yes | yes (fw) | api key | firewall rule already added |
| LM Studio scheduler | 1235 | host | yes | local/LAN reachable | api key | lazy scheduler for chat/code aliases; LiteLLM reaches it at `HOST_LAN_IP:1235` |

## Expose / unexpose (Administrator PowerShell)
```powershell
cd <repo>\scripts
.\expose-services.ps1                    # default: 3000, 7860, 8000
.\expose-services.ps1 -Ports 3000,7860   # custom set
.\expose-services.ps1 -Remove            # tear the proxies+rules back down
```
The LAN IP comes from `HOST_LAN_IP` in `.env` (auto-detected if missing). After
exposing, reach a service from the LAN at `http://<LanIp>:<port>`.

MinerU auth:
- username: `admin`
- password: in `.env` (`MINERU_AUTH_PASS`). To rotate it, run
  `docker run --rm caddy:2 caddy hash-password --plaintext '<new>'`, put the hash
  in `mineru/Caddyfile`, then `stack profile extract` to reload.

SearXNG is deliberately kept off the LAN. OpenWebUI reaches it at `http://searxng:8080`
over the Docker network, and host-side MCP clients use `http://127.0.0.1:8081`.

## Conventions for new services
When adding a service, record it in the table above and decide on LAN exposure:
- User-facing web UI: add its port to `expose-services.ps1`'s default `-Ports`.
- Internal or no-auth backend (vector DBs, gateways): leave it localhost-only,
  never expose it.
- If the host IP changes: update `HOST_LAN_IP` in `.env`, run
  `docker compose up -d`, then re-run `stack expose`.
