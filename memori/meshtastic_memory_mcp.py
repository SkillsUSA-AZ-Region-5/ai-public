#!/usr/bin/env python3
"""
HTTP MCP bridge for Meshtastic memory.

Hermes connects to this server and gets a narrow tool surface:
recall_memory and record_memory. The server stores everything under one
Meshtastic-specific user id in the Meshtastic Mem0 service.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LOG_FILE = ROOT / "memori" / "data" / "meshtastic_memory_mcp.log"
if sys.stdout is None or sys.stderr is None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _logf = LOG_FILE.open("a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stdout or _logf
    sys.stderr = sys.stderr or _logf

MEMORY_URL = os.environ.get(
    "MESHTASTIC_MEMORY_SERVICE_URL",
    f"http://127.0.0.1:{os.environ.get('MESHTASTIC_MEM0_PORT', '8078')}",
).rstrip("/")
TOKEN = os.environ.get("MESHTASTIC_MEM0_SERVICE_TOKEN", "")
USER_ID = os.environ.get("MESHTASTIC_MEMORY_USER_ID", "hermes:meshtastic")
MCP_HOST = os.environ.get("MESHTASTIC_MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MESHTASTIC_MCP_PORT", "8079"))

HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

mcp = FastMCP(
    "meshtastic-memory",
    instructions=(
        "Long-term memory tools for Meshtastic work. Use recall_memory before "
        "answering context-dependent questions and record_memory for durable "
        "facts, decisions, preferences, and troubleshooting results."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
    streamable_http_path="/mcp",
)


def _flatten(items) -> list[str]:
    memories = items.get("memories", []) if isinstance(items, dict) else []
    return [str(m) for m in memories if m]


@mcp.tool()
def recall_memory(query: str, limit: int = 5) -> str:
    """Search Meshtastic long-term memory for information relevant to query."""
    try:
        resp = requests.post(
            f"{MEMORY_URL}/recall",
            json={"user_id": USER_ID, "query": query, "limit": limit},
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        memories = _flatten(resp.json())
    except Exception as e:  # noqa: BLE001
        return f"Memory recall failed: {e}"
    if not memories:
        return "No relevant Meshtastic memories found."
    return "\n".join(f"- {m}" for m in memories)


@mcp.tool()
def record_memory(text: str) -> str:
    """Store a durable Meshtastic fact, decision, preference, or troubleshooting result."""
    text = str(text or "").strip()
    if not text:
        return "Nothing to store."
    try:
        resp = requests.post(
            f"{MEMORY_URL}/record",
            json={"user_id": USER_ID, "text": text},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return f"Memory store failed: {e}"
    return "Stored in Meshtastic memory. Extraction runs in the background."


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
