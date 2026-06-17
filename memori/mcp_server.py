"""
Memory MCP server for Cline/Roo -> thin HTTP client to the local Mem0 service (:8077).

Both Cline and OpenWebUI now share one Mem0-backed memory service (vector recall, fast).
Per-project + shared scoping via user_id:
  - "cline:<MEMORI_PROJECT>"  (this project; MEMORI_PROJECT env, default "default")
  - "cline:global"            (shared across all projects)
recall_memory searches BOTH; record_memory defaults to project, scope="global" promotes.

Run (stdio transport, launched by Cline):  python mcp_server.py
"""
import os
from pathlib import Path
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SERVICE_URL = os.environ.get("MEMORY_SERVICE_URL", "http://localhost:8077")
TOKEN = os.environ.get("MEMORI_SERVICE_TOKEN", "")
PROJECT = os.environ.get("MEMORI_PROJECT", "default")
_H = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

mcp = FastMCP("memori")


def _search(user_id: str, query: str, limit: int = 5):
    try:
        r = requests.post(f"{SERVICE_URL}/recall",
                          json={"user_id": user_id, "query": query, "limit": limit},
                          headers=_H, timeout=30)
        return r.json().get("memories", [])
    except Exception:
        return []


def _recall(query: str) -> str:
    proj = _search(f"cline:{PROJECT}", query)
    glob = _search("cline:global", query)
    blocks = []
    if proj:
        blocks.append(f"## This project ({PROJECT})\n" + "\n".join("- " + m for m in proj))
    if glob:
        blocks.append("## Shared / cross-project\n" + "\n".join("- " + m for m in glob))
    return "\n\n".join(blocks) if blocks else "No relevant memories found."


def _record(text: str, scope: str = "project") -> str:
    uid = "cline:global" if scope == "global" else f"cline:{PROJECT}"
    try:
        requests.post(f"{SERVICE_URL}/record", json={"user_id": uid, "text": text},
                      headers=_H, timeout=15)
        return f"Stored in {scope} (extraction runs in the background)."
    except Exception as e:  # noqa: BLE001
        return f"Failed to store: {e}"


@mcp.tool()
def recall_memory(query: str) -> str:
    """Search long-term memory relevant to `query`, across BOTH this project and the
    shared cross-project pool. Call this at the START of a task."""
    return _recall(query)


@mcp.tool()
def record_memory(text: str, scope: str = "project") -> str:
    """Store a durable fact/decision/preference. scope='project' (default) keeps it to
    this project; scope='global' promotes it to the shared pool every project recalls."""
    return _record(text, scope)


if __name__ == "__main__":
    mcp.run(transport="stdio")
