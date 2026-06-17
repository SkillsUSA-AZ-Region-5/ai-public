#!/usr/bin/env python3
"""
SearXNG web-search MCP server.

Exposes a `web_search` tool backed by the local SearXNG metasearch engine
(published at localhost:8081 by docker-compose). Any MCP client can use it —
Claude Code, Cline, etc. — to get web search against models that don't have it
natively (e.g. local models via LiteLLM).

Register (Claude Code):
  claude mcp add searxng -- <venv-python> <this-file>
Register (Cline): merge cline/mcp-settings.snippet.json's `searxng` entry.

SEARXNG_URL env overrides the default (http://localhost:8081).
"""
import os

import requests
from mcp.server.fastmcp import FastMCP

SEARXNG = os.environ.get("SEARXNG_URL", "http://localhost:8081").rstrip("/")
mcp = FastMCP("searxng")


@mcp.tool()
def web_search(query: str, count: int = 5) -> str:
    """Search the web via the local SearXNG metasearch engine.

    Returns the top results as numbered (title, URL, snippet) blocks. Use for
    current events, documentation, or anything beyond the model's training cutoff.

    Args:
        query: the search query.
        count: how many results to return (1-15, default 5).
    """
    try:
        r = requests.get(f"{SEARXNG}/search",
                         params={"q": query, "format": "json"}, timeout=20)
        r.raise_for_status()
        results = (r.json().get("results") or [])[:max(1, min(count, 15))]
    except Exception as e:  # noqa: BLE001
        return f"search failed ({SEARXNG}): {e}"
    if not results:
        return "No results."
    blocks = []
    for i, x in enumerate(results, 1):
        blocks.append(f"{i}. {(x.get('title') or '').strip()}\n"
                      f"   {x.get('url', '')}\n"
                      f"   {(x.get('content') or '').strip()}")
    return "\n\n".join(blocks)


if __name__ == "__main__":
    mcp.run()
