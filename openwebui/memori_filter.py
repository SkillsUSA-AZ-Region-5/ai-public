"""
title: Memori Memory
author: local-ai-stack
version: 0.1.0
description: Per-account long-term memory backed by the local Memori service (LM Studio).
requirements: requests
"""

# Install in OpenWebUI: Admin Panel -> Functions -> + (New Function) -> paste this ->
# Save -> enable it (globally, or per-model). Memory is scoped per logged-in user
# automatically (uses OpenWebUI's __user__ id), so one OpenWebUI instance gives every
# account its own private memory.
#
# It calls the host memory service (memori/memory_service.py) at the LAN IP, because
# this code runs INSIDE the OpenWebUI container.

import requests
from pydantic import BaseModel, Field


def _flatten(content) -> str:
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    return str(content or "")


class Filter:
    class Valves(BaseModel):
        memory_url: str = Field(
            default="http://HOST_LAN_IP:8077",
            description="Base URL of the host Memori service (host LAN IP).",
        )
        enabled: bool = Field(default=True, description="Master on/off switch.")
        inject_memories: bool = Field(
            default=True, description="Recall + inject memories before the LLM call."
        )
        record_turns: bool = Field(
            default=True, description="Record each turn into memory after the LLM reply."
        )
        recall_limit: int = Field(default=5, description="Max memories to inject.")
        recall_timeout: int = Field(
            default=20, description="Seconds to wait for recall (it can take ~9s)."
        )
        service_token: str = Field(
            default="", description="Shared secret for the Memori service (Bearer token)."
        )

    def __init__(self):
        self.valves = self.Valves()

    def _headers(self):
        return {"Authorization": f"Bearer {self.valves.service_token}"} if self.valves.service_token else {}

    def _uid(self, __user__):
        if not __user__:
            return None
        return __user__.get("id") or __user__.get("email") or None

    # Runs BEFORE the request goes to the LLM.
    def inlet(self, body: dict, __user__: dict = None) -> dict:
        if not (self.valves.enabled and self.valves.inject_memories):
            return body
        uid = self._uid(__user__)
        if not uid:
            return body
        messages = body.get("messages", [])
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"), None
        )
        if not last_user:
            return body
        try:
            resp = requests.post(
                f"{self.valves.memory_url}/recall",
                json={
                    "user_id": uid,
                    "query": _flatten(last_user.get("content")),
                    "limit": self.valves.recall_limit,
                },
                headers=self._headers(),
                timeout=self.valves.recall_timeout,
            )
            memories = resp.json().get("memories", [])
        except Exception:
            memories = []
        if memories:
            ctx = "Relevant long-term memory about this user:\n" + "\n".join(
                f"- {m}" for m in memories
            )
            messages.insert(0, {"role": "system", "content": ctx})
            body["messages"] = messages
        return body

    # Runs AFTER the LLM reply.
    def outlet(self, body: dict, __user__: dict = None) -> dict:
        if not (self.valves.enabled and self.valves.record_turns):
            return body
        uid = self._uid(__user__)
        if not uid:
            return body
        messages = body.get("messages", [])
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"), None
        )
        last_asst = next(
            (m for m in reversed(messages) if m.get("role") == "assistant"), None
        )
        parts = []
        if last_user:
            parts.append("User: " + _flatten(last_user.get("content")))
        if last_asst:
            parts.append("Assistant: " + _flatten(last_asst.get("content")))
        text = "\n".join(parts).strip()
        if text:
            try:
                requests.post(
                    f"{self.valves.memory_url}/record",
                    json={"user_id": uid, "text": text},
                    headers=self._headers(),
                    timeout=8,
                )
            except Exception:
                pass
        return body
