"""
title: Web Fetch
author: local-ai-stack
version: 0.1.0
description: Fetch and read the text content of a specific URL (complements SearXNG search).
requirements: requests
"""
import re
import requests
from pydantic import BaseModel, Field


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|head).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


class Tools:
    class Valves(BaseModel):
        max_chars: int = Field(default=6000, description="Max characters of page text to return.")
        timeout: int = Field(default=15, description="Request timeout (seconds).")

    def __init__(self):
        self.valves = self.Valves()

    def fetch_url(self, url: str) -> str:
        """
        Fetch a web page and return its readable text content. Use this when the user
        gives a specific URL or asks you to read/summarize a page.
        :param url: The full URL to fetch (must start with http:// or https://).
        :return: The page's plain-text content (truncated), or an error string.
        """
        if not re.match(r"^https?://", url):
            return "Error: url must start with http:// or https://"
        try:
            r = requests.get(
                url, timeout=self.valves.timeout,
                headers={"User-Agent": "Mozilla/5.0 (OpenWebUI WebFetch)"},
            )
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return f"Error fetching {url}: {e}"
        ctype = r.headers.get("content-type", "")
        text = r.text if "html" in ctype or "text" in ctype else f"[non-text content: {ctype}]"
        if "html" in ctype:
            text = _html_to_text(text)
        return text[: self.valves.max_chars]
