#!/usr/bin/env python3
"""
Lazy scheduler proxy for LM Studio chat/code profiles.

LiteLLM routes selected LM Studio aliases here instead of directly to :1234.
The first request for a cold scheduled model starts the matching stack profile
in the background and returns a 503 warming response. Later requests forward to
LM Studio once the profile is loaded. Warm models stay resident until their
profile has been idle for its TTL.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
LOG = ROOT / "manage" / "lmstudio-scheduler-proxy.log"


def _load_env() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

sys.path.insert(0, str(ROOT / "manage"))
import stackctl  # noqa: E402

PROXY_HOST = os.environ.get("LMSTUDIO_SCHEDULER_HOST", "0.0.0.0")
PROXY_PORT = int(os.environ.get("LMSTUDIO_SCHEDULER_PORT", "1235"))
LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_DIRECT_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
LMSTUDIO_ORIGIN = LMSTUDIO_BASE_URL[:-3] if LMSTUDIO_BASE_URL.endswith("/v1") else LMSTUDIO_BASE_URL
LMSTUDIO_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_IDLE_SECONDS = int(os.environ.get("LMSTUDIO_SCHEDULER_IDLE_SECONDS", "5400"))
CHECK_SECONDS = int(os.environ.get("LMSTUDIO_SCHEDULER_CHECK_SECONDS", "60"))

PROFILE_BY_MODEL = {
    # Chat profile.
    "google/gemma-4-26b-a4b-qat": "chat",
    # Code profile, including app-facing aliases in case LiteLLM passes them through.
    "qwen/qwen3.6-35b-a3b": "code",
    "qwen2.5-3b-instruct": "code",
    "chat": "code",
    "hermes": "code",
    "hermes-subagent": "code",
    "cline": "code",
    "qwen-code": "code",
    "claude-local-main": "code",
    "claude-local-fast": "code",
}

PROFILE_TARGET = {
    "chat": "google/gemma-4-26b-a4b-qat",
    "code": "qwen/qwen3.6-35b-a3b",
}

FORWARD_MODEL_BY_MODEL = {
    "chat": PROFILE_TARGET["code"],
    "hermes": PROFILE_TARGET["code"],
    "cline": PROFILE_TARGET["code"],
    "qwen-code": PROFILE_TARGET["code"],
    "claude-local-main": PROFILE_TARGET["code"],
    "claude-local-fast": PROFILE_TARGET["code"],
}

PROFILE_WARMUP_SECONDS = {
    "chat": int(os.environ.get("LMSTUDIO_CHAT_WARMUP_SECONDS", "120")),
    "code": int(os.environ.get("LMSTUDIO_CODE_WARMUP_SECONDS", "240")),
}

PROFILE_IDLE_SECONDS = {
    "chat": int(os.environ.get("LMSTUDIO_CHAT_IDLE_SECONDS", str(DEFAULT_IDLE_SECONDS))),
    "code": int(os.environ.get("LMSTUDIO_CODE_IDLE_SECONDS", str(DEFAULT_IDLE_SECONDS))),
}

PROFILE_UNLOAD_MODELS = {
    # Keep the memory core loaded; unload only the large scheduled main model.
    "chat": ["google/gemma-4-26b-a4b-qat"],
    # Code also owns the CPU Hermes sub-agent model. The small brain/embed stay loaded.
    "code": ["qwen/qwen3.6-35b-a3b", "qwen2.5-3b-instruct"],
}

_state_lock = threading.Lock()
_load_lock = threading.Lock()
_loading: dict[str, bool] = {name: False for name in PROFILE_TARGET}
_last_activity: dict[str, float | None] = {name: None for name in PROFILE_TARGET}
_active_requests: dict[str, int] = {name: 0 for name in PROFILE_TARGET}


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{stamp} {msg}\n")


def _loaded_ids() -> set[str]:
    return stackctl._loaded_model_ids()  # noqa: SLF001 - local stack helper


def _target_loaded(profile: str) -> bool:
    target = PROFILE_TARGET[profile]
    return any(target in model_id for model_id in _loaded_ids())


def _profile_for_model(model: str | None) -> str | None:
    if not model:
        return None
    model = model.removeprefix("openai/")
    return PROFILE_BY_MODEL.get(model)


def _mark_activity(profile: str) -> None:
    with _state_lock:
        _last_activity[profile] = time.time()


def _idle_seconds(profile: str) -> int | None:
    with _state_lock:
        last = _last_activity.get(profile)
    if last is None:
        return None
    return max(0, int(time.time() - last))


def _start_profile_async(profile: str) -> None:
    _mark_activity(profile)
    if _target_loaded(profile):
        return
    with _load_lock:
        if _loading.get(profile) or _target_loaded(profile):
            return
        _loading[profile] = True
        _log(f"warming profile {profile}")

    def load() -> None:
        try:
            stackctl.apply_profile(profile)
            _log(f"profile {profile} loaded")
        except Exception as e:  # noqa: BLE001
            _log(f"profile {profile} load failed: {e}")
        finally:
            with _state_lock:
                _last_activity[profile] = time.time()
            with _load_lock:
                _loading[profile] = False

    threading.Thread(target=load, daemon=True).start()


def _unload_profile(profile: str, reason: str) -> None:
    _log(f"unloading profile {profile}: {reason}")
    for model in PROFILE_UNLOAD_MODELS.get(profile, []):
        try:
            stackctl.lms_unload(model)
        except Exception as e:  # noqa: BLE001
            _log(f"unload failed for {model}: {e}")
    with _state_lock:
        _last_activity[profile] = None


def _idle_reaper() -> None:
    while True:
        time.sleep(max(5, CHECK_SECONDS))
        for profile, timeout in PROFILE_IDLE_SECONDS.items():
            if timeout <= 0:
                continue
            with _state_lock:
                active = _active_requests.get(profile, 0)
                last = _last_activity.get(profile)
            if active or _loading.get(profile) or last is None:
                continue
            idle = time.time() - last
            if idle >= timeout and _target_loaded(profile):
                _unload_profile(profile, f"{int(idle)}s idle, timeout {timeout}s")


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict, extra_headers: dict | None = None) -> None:
    data = json.dumps(body).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            handler.send_header(k, str(v))
        handler.end_headers()
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        pass


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not LMSTUDIO_API_KEY:
        return True
    auth = handler.headers.get("Authorization", "")
    return auth == f"Bearer {LMSTUDIO_API_KEY}"


def _model_from_body(body: bytes) -> tuple[str | None, bytes]:
    if not body:
        return None, body
    try:
        payload: dict[str, Any] = json.loads(body)
    except Exception:
        return None, body
    model = payload.get("model")
    profile = _profile_for_model(model)
    if profile:
        model_name = str(model).removeprefix("openai/")
        payload["model"] = FORWARD_MODEL_BY_MODEL.get(model_name, model_name)
        return payload["model"], json.dumps(payload).encode("utf-8")
    return str(model) if model else None, body


class Handler(BaseHTTPRequestHandler):
    server_version = "LMStudioSchedulerProxy/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        _log(fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/"):
            body = {"ok": True, "proxy": True, "lmstudio_base_url": LMSTUDIO_BASE_URL, "profiles": {}}
            for profile in PROFILE_TARGET:
                body["profiles"][profile] = {
                    "loaded": _target_loaded(profile),
                    "loading": _loading.get(profile, False),
                    "idle_seconds": _idle_seconds(profile),
                    "idle_timeout_seconds": PROFILE_IDLE_SECONDS[profile],
                    "warmup_seconds": PROFILE_WARMUP_SECONDS[profile],
                }
            _json_response(self, 200, body)
            return
        self._forward()

    def do_POST(self) -> None:  # noqa: N802
        self._forward()

    def _forward(self) -> None:
        global _active_requests
        if not _authorized(self):
            _json_response(self, 401, {"error": {"message": "Unauthorized", "type": "auth_error"}})
            return

        body = b""
        if self.command in ("POST", "PUT", "PATCH"):
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""
        model, body = _model_from_body(body)
        profile = _profile_for_model(model)
        if profile and not _target_loaded(profile):
            _start_profile_async(profile)
            retry = PROFILE_WARMUP_SECONDS[profile]
            _json_response(self, 503, {
                "error": {
                    "message": f"LM Studio profile '{profile}' is loading. Retry in about {retry} seconds.",
                    "type": "model_warming",
                    "code": "lmstudio_profile_warming",
                    "profile": profile,
                    "model": PROFILE_TARGET[profile],
                    "retry_after_seconds": retry,
                }
            }, {"Retry-After": retry})
            return

        if profile:
            _mark_activity(profile)
            with _state_lock:
                _active_requests[profile] += 1
        try:
            self._proxy_to_lmstudio(body)
        finally:
            if profile:
                with _state_lock:
                    _active_requests[profile] = max(0, _active_requests[profile] - 1)
                    _last_activity[profile] = time.time()

    def _proxy_to_lmstudio(self, body: bytes) -> None:
        url = LMSTUDIO_ORIGIN + self.path
        _log(f"forward {self.command} {self.path} -> {url}")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "content-length"}}
        if LMSTUDIO_API_KEY:
            headers["Authorization"] = f"Bearer {LMSTUDIO_API_KEY}"
        req = urllib.request.Request(url, data=body if self.command != "GET" else None, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=1800) as r:
                self.send_response(r.status)
                for k, v in r.headers.items():
                    if k.lower() not in {"transfer-encoding", "connection", "content-length"}:
                        self.send_header(k, v)
                self.end_headers()
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # noqa: BLE001
            _json_response(self, 502, {"error": {"message": str(e), "type": "backend_error"}})


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    _log(f"proxy starting on {PROXY_HOST}:{PROXY_PORT}, lmstudio={LMSTUDIO_BASE_URL}")
    threading.Thread(target=_idle_reaper, daemon=True).start()
    server = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
