"""
LiteLLM success callback -> writes token usage to InfluxDB (v2 line protocol).
Mounted into the LiteLLM container at /app/usage_callback.py; referenced from
config.yaml as  callbacks: ["usage_callback.handler"]  (PYTHONPATH=/app).

Per-user attribution: OpenWebUI (ENABLE_FORWARD_USER_INFO_HEADERS=true) forwards
X-OpenWebUI-User-* headers; LiteLLM stashes the request in kwargs, so we read them.

Writes one point per completion to measurement `llm_usage`:
  tags  : user, app, model, status   (app = which client: openwebui, hermes,
          cline, claude-code, qwen-cli; derived from the requested alias / headers)
  fields: prompt_tokens, completion_tokens, total_tokens, requests(=1), latency_s
Configure via env (set in docker-compose from .env): INFLUX_URL, INFLUX_ORG,
INFLUX_BUCKET, INFLUX_TOKEN, INFLUX_MEASUREMENT. If unset, the callback no-ops.
"""
import json
import os
import re
import time

import httpx
from litellm.integrations.custom_logger import CustomLogger

INFLUX_URL = os.environ.get("INFLUX_URL", "").rstrip("/")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
MEASUREMENT = os.environ.get("INFLUX_MEASUREMENT", "llm_usage")
# Also feed the local stackctl dashboard (works even without InfluxDB configured).
USAGE_INGEST_URL = os.environ.get("USAGE_INGEST_URL", "")
USAGE_INGEST_TOKEN = os.environ.get("USAGE_INGEST_TOKEN", "")


def _esc(v: str) -> str:
    """Escape a line-protocol tag value (space, comma, equals)."""
    return str(v).replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _headers(kwargs) -> dict:
    md = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    h = md.get("headers") or (kwargs.get("proxy_server_request") or {}).get("headers") or {}
    return {str(k).lower(): v for k, v in (h or {}).items()}


def _user(kwargs) -> str:
    h = _headers(kwargs)
    for key in ("x-openwebui-user-email", "x-openwebui-user-name", "x-openwebui-user-id"):
        if h.get(key):
            return h[key]
    u = kwargs.get("user") or "anon"
    # Some clients shove a JSON blob (e.g. {"device_id": "..."}) into `user`. Collapse it
    # to a short, readable tag so it doesn't blow up the dashboard / Influx series.
    if isinstance(u, str) and u.lstrip().startswith("{"):
        m = re.search(r'"(?:device_id|id)"\s*:\s*"([^"]+)', u)
        if m:
            return f"device:{m.group(1)[:12]}"
        try:
            obj = json.loads(u)
            did = obj.get("device_id") or obj.get("id") or ""
            return f"device:{str(did)[:12]}" if did else "device"
        except Exception:
            return "device"
    return u


# Which alias maps to which app. The dashboard's "by model" tag carries the *resolved*
# model (all qwen apps collapse into one bar), so we attribute by the requested alias
# (LiteLLM's model_group) instead. Give each app its own alias in litellm/config.yaml.
APP_BY_ALIAS = {
    "claude-local-main": "claude-code",
    "claude-local-fast": "claude-code",
    "qwen-code": "qwen-cli",
    "hermes": "hermes",
    "cline": "cline",
}

OPENWEBUI_ALIASES = {
    "chat",
    "brain",
    "embed",
    "google/gemma-4-26b-a4b-qat",
    "google/gemma-4-12b-qat",
    "qwen/qwen3.6-35b-a3b",
    "text-embedding-nomic-embed-text-v1.5",
}


def _alias(kwargs) -> str:
    md = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    return md.get("model_group") or kwargs.get("model_group") or ""


def _app(kwargs) -> str:
    # OpenWebUI forwards per-user headers; anything carrying them is OpenWebUI.
    h = _headers(kwargs)
    if any(h.get(k) for k in ("x-openwebui-user-email", "x-openwebui-user-name", "x-openwebui-user-id")):
        return "openwebui"
    alias = _alias(kwargs)
    if alias in APP_BY_ALIAS:
        return APP_BY_ALIAS[alias]
    # OpenWebUI's own background calls (title/tag/search-query gen) hit the chat models
    # without a user header; attribute those to OpenWebUI too.
    if alias in OPENWEBUI_ALIASES:
        return "openwebui"
    return alias or "other"


def _usage(response_obj):
    u = getattr(response_obj, "usage", None)
    if u is None and isinstance(response_obj, dict):
        u = response_obj.get("usage")
    if u is None:
        return 0, 0
    if isinstance(u, dict):
        return int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    return int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0)


class UsageCallback(CustomLogger):
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._send(kwargs, response_obj, start_time, end_time)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._send(kwargs, response_obj, start_time, end_time)

    def _send(self, kwargs, response_obj, start_time, end_time):
        pt, ct = _usage(response_obj)
        user = _user(kwargs)
        app = _app(kwargs)
        model = kwargs.get("model") or "unknown"
        lat = None
        try:
            lat = (end_time - start_time).total_seconds()
        except Exception:
            pass
        # two independent best-effort sinks: InfluxDB and the local dashboard
        self._to_influx(user, app, model, pt, ct, lat)
        self._to_dashboard(user, app, model, pt, ct, lat)

    def _to_influx(self, user, app, model, pt, ct, lat):
        if not (INFLUX_URL and INFLUX_TOKEN and INFLUX_BUCKET):
            return
        try:
            fields = [f"prompt_tokens={pt}i", f"completion_tokens={ct}i",
                      f"total_tokens={pt + ct}i", "requests=1i"]
            if lat is not None:
                fields.append(f"latency_s={lat}")
            line = f"{MEASUREMENT},user={_esc(user)},app={_esc(app)},model={_esc(model)},status=success " \
                   + ",".join(fields) + f" {time.time_ns()}"
            httpx.post(
                f"{INFLUX_URL}/api/v2/write",
                params={"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "ns"},
                headers={"Authorization": f"Token {INFLUX_TOKEN}",
                         "Content-Type": "text/plain; charset=utf-8"},
                content=line, timeout=3.0,
            )
        except Exception as exc:  # never let metering break a completion
            print("usage_callback influx error:", exc, flush=True)

    def _to_dashboard(self, user, app, model, pt, ct, lat):
        if not USAGE_INGEST_URL:
            return
        try:
            httpx.post(USAGE_INGEST_URL,
                       headers={"X-Usage-Token": USAGE_INGEST_TOKEN},
                       json={"user": user, "app": app, "model": model, "prompt_tokens": pt,
                             "completion_tokens": ct, "latency_s": lat},
                       timeout=2.0)
        except Exception as exc:
            print("usage_callback dashboard error:", exc, flush=True)


handler = UsageCallback()
