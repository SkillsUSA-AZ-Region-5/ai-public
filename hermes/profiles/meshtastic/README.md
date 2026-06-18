# Meshtastic Hermes Profile

This profile gives Hermes a separate Meshtastic memory path:

- Meshtastic Mem0 API: `http://localhost:8078`
- Meshtastic MCP bridge: `http://localhost:8079/mcp`
- Qdrant collection: `mem0_meshtastic`
- Memory user id: `hermes:meshtastic`

## Start Memory

```powershell
stack meshtastic start
stack meshtastic status
```

Bulk markdown can be preloaded into the separate Meshtastic memory collection:

```powershell
stack meshtastic import-md C:\path\to\markdown --dry-run
stack meshtastic import-md C:\path\to\markdown
```

By default, markdown is indexed directly as source reference chunks. Use
`--via-mem0` only when the content should go through the Mem0 extraction model
as ordinary long-term memory.

The service reads these values from `.env`:

```dotenv
MESHTASTIC_MEM0_SERVICE_TOKEN=msk_mesh_CHANGE-ME
MESHTASTIC_MEM0_PORT=8078
MESHTASTIC_MCP_PORT=8079
MESHTASTIC_MEM0_COLLECTION=mem0_meshtastic
MESHTASTIC_MEMORY_USER_ID=hermes:meshtastic
```

The profile expects Hermes to reach the MCP bridge through the Windows host LAN
IP. In `config.snippet.yaml`, replace `<HOST_LAN_IP>` with the value from `.env`.

## Apply To Hermes

Merge `config.snippet.yaml` into `/root/.hermes/config.yaml`, keep the real
`LITELLM_KEY_HERMES` value, then restart Hermes:

```bash
cd /opt/hermes-agent
docker compose restart gateway
```

After restart, ask Hermes which MCP tools are available. The memory tools should
appear with the `mcp_meshtastic_memory_` prefix.
