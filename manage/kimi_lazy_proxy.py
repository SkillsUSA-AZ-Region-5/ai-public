#!/usr/bin/env python3
"""
Always-on lightweight proxy for Kimi-K2.7-Code.

LiteLLM talks to this proxy on 127.0.0.1:8095 via the existing LAN portproxy.
The real llama-server backend stays down until a request arrives, then loads on
127.0.0.1:8096. The first request gets a 503 "warming up" response; retry once
the backend has finished loading. Once loaded, the backend is kept warm until it
has been idle for KIMI_IDLE_TIMEOUT_SECONDS, default 90 minutes.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
LOG = ROOT / "manage" / "kimi-lazy-proxy.log"
BACKEND_LOG = ROOT / "manage" / "kimi-server.log"


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

DEFAULT_KIMI_SERVER = Path.home() / "llamacpp" / "llama-server.exe"
DEFAULT_KIMI_MODEL = (
    Path.home()
    / "models"
    / "Kimi-K2.7-Code-GGUF"
    / "UD-Q2_K_XL"
    / "Kimi-K2.7-Code-UD-Q2_K_XL-00001-of-00008.gguf"
)

KIMI_SERVER = Path(os.environ.get("KIMI_SERVER", str(DEFAULT_KIMI_SERVER)))
KIMI_MODEL = Path(os.environ.get(
    "KIMI_MODEL",
    str(DEFAULT_KIMI_MODEL),
))
KIMI_THREADS = os.environ.get("KIMI_THREADS", "192")
KIMI_CPU_RANGE = os.environ.get("KIMI_CPU_RANGE", "0-191")
KIMI_CTX = os.environ.get("KIMI_CTX", "8192")
PROXY_HOST = os.environ.get("KIMI_PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("KIMI_PROXY_PORT", "8095"))
BACKEND_HOST = os.environ.get("KIMI_BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("KIMI_BACKEND_PORT", "8096"))
IDLE_TIMEOUT_SECONDS = int(os.environ.get("KIMI_IDLE_TIMEOUT_SECONDS", "5400"))
IDLE_CHECK_SECONDS = int(os.environ.get("KIMI_IDLE_CHECK_SECONDS", "60"))
BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
MODEL_ID = "kimi/kimi-k2.7-code"

_starting = False
_backend_proc: subprocess.Popen | None = None
_last_activity: float | None = None
_active_requests = 0
_start_lock = threading.Lock()
_activity_lock = threading.Lock()


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{stamp} {msg}\n")


def _backend_health(timeout: float = 0.25) -> bool:
    try:
        with urllib.request.urlopen(f"{BACKEND_URL}/health", timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _mark_activity() -> None:
    global _last_activity
    with _activity_lock:
        _last_activity = time.time()


def _idle_seconds() -> int | None:
    with _activity_lock:
        if _last_activity is None:
            return None
        return max(0, int(time.time() - _last_activity))


def _backend_procs() -> list:
    if not psutil:
        return []
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info.get("name") or "").lower()
            cl = " ".join(p.info["cmdline"] or []).lower()
            if "llama-server" in name and (str(BACKEND_PORT) in cl or KIMI_MODEL.name.lower() in cl):
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def _terminate_backend(reason: str) -> None:
    global _backend_proc, _last_activity
    killed = 0
    if _backend_proc and _backend_proc.poll() is None:
        try:
            _backend_proc.terminate()
            killed += 1
        except Exception as e:  # noqa: BLE001
            _log(f"backend terminate via handle failed: {e}")
    for p in _backend_procs():
        try:
            p.terminate()
            killed += 1
        except Exception as e:  # noqa: BLE001
            _log(f"backend terminate pid {getattr(p, 'pid', '?')} failed: {e}")
    if psutil:
        try:
            psutil.wait_procs(_backend_procs(), timeout=10)
        except Exception:
            pass
    _backend_proc = None
    with _activity_lock:
        _last_activity = None
    _log(f"backend idle stop: {reason}; terminated {killed} process(es)")


def _hidden_kwargs() -> dict:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        "startupinfo": startupinfo,
    }


def _backend_cmd() -> list[str]:
    cmd = [
        str(KIMI_SERVER),
        "-m", str(KIMI_MODEL),
        "--numa", "distribute",
        "-t", str(KIMI_THREADS),
    ]
    if KIMI_CPU_RANGE:
        cmd += ["--cpu-range", KIMI_CPU_RANGE, "--cpu-strict", "1"]
    cmd += ["-c", str(KIMI_CTX), "--host", BACKEND_HOST, "--port", str(BACKEND_PORT), "--no-mmap"]
    return cmd


def _start_backend_once() -> None:
    global _starting, _backend_proc
    _mark_activity()
    if _backend_health():
        return
    with _start_lock:
        if _starting or _backend_health():
            return
        if not KIMI_SERVER.exists() or not KIMI_MODEL.exists():
            _log(f"backend not started: missing server={KIMI_SERVER.exists()} model={KIMI_MODEL.exists()}")
            return
        _starting = True
        BACKEND_LOG.parent.mkdir(parents=True, exist_ok=True)
        logf = BACKEND_LOG.open("ab")
        _backend_proc = subprocess.Popen(
            _backend_cmd(),
            stdout=logf,
            stderr=logf,
            cwd=str(KIMI_SERVER.parent),
            **_hidden_kwargs(),
        )
        _log(f"started backend on {BACKEND_URL}")

    def watch() -> None:
        global _starting
        deadline = time.time() + 480
        while time.time() < deadline:
            if _backend_health(timeout=2):
                _log("backend healthy")
                _starting = False
                return
            time.sleep(2)
        _log("backend still not healthy after 480s")
        _starting = False

    threading.Thread(target=watch, daemon=True).start()


def _idle_reaper() -> None:
    global _last_activity
    if IDLE_TIMEOUT_SECONDS <= 0:
        _log("idle reaper disabled")
        return
    while True:
        time.sleep(max(5, IDLE_CHECK_SECONDS))
        with _activity_lock:
            active = _active_requests
            idle = None if _last_activity is None else time.time() - _last_activity
            if _last_activity is None and _backend_health(timeout=0.2):
                idle = 0
                _last_activity = time.time()
        if _starting or active:
            continue
        if idle is not None and idle >= IDLE_TIMEOUT_SECONDS and (_backend_health(timeout=0.2) or _backend_procs()):
            _terminate_backend(f"{int(idle)}s idle, timeout {IDLE_TIMEOUT_SECONDS}s")


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict) -> None:
    data = json.dumps(body).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "KimiLazyProxy/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        _log(fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/"):
            _json_response(self, 200, {
                "ok": True,
                "proxy": True,
                "backend": _backend_health(timeout=0.2),
                "starting": _starting,
                "backend_url": BACKEND_URL,
                "idle_timeout_seconds": IDLE_TIMEOUT_SECONDS,
                "idle_seconds": _idle_seconds(),
            })
            return
        if self.path.startswith("/v1/models") and not _backend_health():
            _json_response(self, 200, {
                "object": "list",
                "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local"}],
            })
            return
        self._forward()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/v1/") and not _backend_health():
            _mark_activity()
            _start_backend_once()
            _json_response(self, 503, {
                "error": {
                    "message": "Kimi is loading into RAM. Retry in about 400 seconds.",
                    "type": "model_warming",
                    "code": "kimi_warming",
                }
            })
            return
        self._forward()

    def _forward(self) -> None:
        global _active_requests, _last_activity
        _mark_activity()
        url = BACKEND_URL + self.path
        body = None
        if self.command in ("POST", "PUT", "PATCH"):
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""
        headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "content-length"}}
        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        with _activity_lock:
            _active_requests += 1
        try:
            with urllib.request.urlopen(req, timeout=1800) as r:
                data = r.read()
                self.send_response(r.status)
                for k, v in r.headers.items():
                    if k.lower() not in {"transfer-encoding", "connection", "content-length"}:
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # noqa: BLE001
            _json_response(self, 502, {"error": {"message": str(e), "type": "backend_error"}})
        finally:
            with _activity_lock:
                _active_requests = max(0, _active_requests - 1)
                _last_activity = time.time()


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    _log(f"proxy starting on {PROXY_HOST}:{PROXY_PORT}, backend {BACKEND_URL}, idle_timeout={IDLE_TIMEOUT_SECONDS}s")
    threading.Thread(target=_idle_reaper, daemon=True).start()
    server = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
