#!/usr/bin/env python3
"""
stackctl - manage the local AI stack (LM Studio models + Docker services) by workload.

Design: the functions in the CORE section are plain Python (no CLI deps), so a future
FastAPI web UI can import and call them directly. Typer below is just the CLI surface.

Runs on Windows; shells out to `lms`, `nvidia-smi`, and `wsl ... docker`.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import ctypes
import uuid
from pathlib import Path
from typing import Optional

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

# ---------------------------------------------------------------- config
# Self-locating: this file lives in <repo>/manage/, so the repo root is one level up.
# Override with STACK_ROOT if you ever need to. The repo's .env supplies the rest
# (HOST_LAN_IP, WSL_DISTRO, ...) without clobbering anything already in the env.
ROOT = Path(os.environ.get("STACK_ROOT") or Path(__file__).resolve().parent.parent)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass
MAIN_COMPOSE = ROOT / "docker-compose.yml"
MINERU_COMPOSE = ROOT / "mineru" / "docker-compose.yml"          # CPU
MINERU_GPU_COMPOSE = ROOT / "mineru" / "docker-compose.gpu.yml"  # GPU (pipeline/cuda)
MINERU_BULK = ROOT / "mineru" / "bulk_mineru.py"
OPENWEBUI_IMPORT = ROOT / "openwebui" / "import_knowledge.py"
OPENWEBUI_SMOKE = ROOT / "openwebui" / "smoke_retrieval.py"
SKILLSUSA_CARDS = ROOT / "skillsusa" / "generate_knowledge_cards.py"
SKILLSUSA_SMOKE_CASES = ROOT / "skillsusa" / "smoke-cases.json"
EXPOSE_SCRIPT = ROOT / "scripts" / "expose-services.ps1"
MEM0_PYW = ROOT / "memori" / ".venv" / "Scripts" / "pythonw.exe"
MEM0_SCRIPT = ROOT / "memori" / "mem0_service.py"
MEM0_LOG = ROOT / "memori" / "data" / "mem0_service.log"
MEM0_URL = "http://localhost:8077"
MESHTASTIC_MEM0_SCRIPT = ROOT / "memori" / "meshtastic_mem0_service.py"
MESHTASTIC_MEMORY_MCP_SCRIPT = ROOT / "memori" / "meshtastic_memory_mcp.py"
MESHTASTIC_MEM0_PORT = int(os.environ.get("MESHTASTIC_MEM0_PORT", "8078"))
MESHTASTIC_MCP_PORT = int(os.environ.get("MESHTASTIC_MCP_PORT", "8079"))
MESHTASTIC_MEM0_COLLECTION = os.environ.get("MESHTASTIC_MEM0_COLLECTION", "mem0_meshtastic")
MESHTASTIC_MEM0_URL = f"http://localhost:{MESHTASTIC_MEM0_PORT}"
MESHTASTIC_MCP_URL = f"http://localhost:{MESHTASTIC_MCP_PORT}/mcp"
MESHTASTIC_MEM0_LOG = ROOT / "memori" / "data" / "meshtastic_mem0_service.log"
MESHTASTIC_MCP_LOG = ROOT / "memori" / "data" / "meshtastic_memory_mcp.log"
QDRANT_URL = "http://localhost:6333"
WEB_SCRIPT = ROOT / "manage" / "webapp.py"      # the dashboard runs on the same venv
WEB_LOG = ROOT / "manage" / "webapp.log"
WEB_URL = "http://127.0.0.1:8090"
LM_SCHEDULER_SCRIPT = ROOT / "manage" / "lmstudio_scheduler_proxy.py"
LM_SCHEDULER_LOG = ROOT / "manage" / "lmstudio-scheduler-proxy.log"
LM_SCHEDULER_PORT = int(os.environ.get("LMSTUDIO_SCHEDULER_PORT", "1235"))
LM_SCHEDULER_IDLE_SECONDS = int(os.environ.get("LMSTUDIO_SCHEDULER_IDLE_SECONDS", "5400"))
LM_SCHEDULER_URL = f"http://127.0.0.1:{LM_SCHEDULER_PORT}"
MONITOR_SCRIPT = ROOT / "manage" / "flight_recorder.py"
MONITOR_LOG = ROOT / "manage" / "stack-monitor.log"
# Kimi-K2.7-Code: a standalone llama.cpp backend on the HOST (CPU+RAM only), behind
# a tiny lazy proxy. NOT an LM Studio model and NOT a GPU profile. The 339GB MoE cannot
# fit VRAM, so it idles unloaded and serves from RAM only when requested.
# LiteLLM reaches the proxy via LAN IP + portproxy :8095; backend binds localhost :8096.
# Paths default under the current Windows user but are .env-overridable.
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
    str(DEFAULT_KIMI_MODEL)))
KIMI_THREADS = os.environ.get("KIMI_THREADS", "192")  # all logical CPUs (both sockets)
# Windows confines a process to one 64+ CPU "processor group" by default, so a bare -t left
# every thread on socket 0 (the other socket measured idle during generation). --cpu-range
# spanning all logical CPUs + --cpu-strict 1 forces threads onto BOTH sockets/groups. It's a
# strict win (prefill +~24%) though gen only +~6%: this MoE is memory-bandwidth bound, and the
# 339GB model spans both NUMA nodes so cross-socket (Infinity Fabric) traffic is the real wall.
# Set KIMI_CPU_RANGE="" to disable the affinity flags (e.g. on a single-socket box).
KIMI_CPU_RANGE = os.environ.get("KIMI_CPU_RANGE", "0-191")
KIMI_CTX = os.environ.get("KIMI_CTX", "8192")
KIMI_PROXY_PORT = int(os.environ.get("KIMI_PROXY_PORT", "8095"))
KIMI_BACKEND_PORT = int(os.environ.get("KIMI_BACKEND_PORT", "8096"))
KIMI_IDLE_TIMEOUT_SECONDS = int(os.environ.get("KIMI_IDLE_TIMEOUT_SECONDS", "5400"))
KIMI_PORT = KIMI_BACKEND_PORT
KIMI_URL = f"http://127.0.0.1:{KIMI_PORT}"
KIMI_LOG = ROOT / "manage" / "kimi-server.log"
KIMI_PROXY_SCRIPT = ROOT / "manage" / "kimi_lazy_proxy.py"
KIMI_PROXY_LOG = ROOT / "manage" / "kimi-lazy-proxy.log"
KIMI_PROXY_URL = f"http://127.0.0.1:{KIMI_PROXY_PORT}"
WSL = ["wsl", "-d", os.environ.get("WSL_DISTRO", "Ubuntu-24.04"), "-u", "root", "--"]
console = Console()


def _wsl_path(p: Path) -> str:
    s = str(p)                                  # C:\a\b -> /mnt/c/a/b
    return "/mnt/" + s[0].lower() + s[2:].replace("\\", "/")


def _has_console() -> bool:
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return True


def _hidden_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def _run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    kwargs = {"capture_output": capture, "text": True}
    # The dashboard runs under pythonw.exe. Without this, child console helpers
    # (powershell, wsl, docker, nvidia-smi) can flash windows and steal focus in
    # the active RDP session on every status poll.
    if os.name == "nt" and (capture or not _has_console()):
        kwargs.update(_hidden_subprocess_kwargs())
    return subprocess.run(cmd, **kwargs)


def _json_request(url: str, method: str = "GET", body: Optional[dict] = None,
                  headers: Optional[dict] = None, timeout: float = 10.0) -> dict:
    data = None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw or b"{}")


def _load_env_file() -> dict[str, str]:
    env_path = ROOT / ".env"
    out = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# ================================================================ CORE
# (framework-agnostic - importable by a web UI / API later)
def gpu_status() -> list[dict]:
    r = _run(["nvidia-smi", "--query-gpu=index,memory.used,memory.total,utilization.gpu",
              "--format=csv,noheader,nounits"])
    out = []
    for line in r.stdout.strip().splitlines():
        idx, used, total, util = [x.strip() for x in line.split(",")]
        out.append({"gpu": int(idx), "used_mb": int(used), "total_mb": int(total),
                    "free_mb": int(total) - int(used), "util_pct": int(util)})
    return out


def _cpu_name() -> str:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except Exception:
        return platform.processor() or "CPU"


def _fmt_uptime(secs: int) -> str:
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    return (f"{d}d " if d else "") + f"{h}h {m}m"


@functools.lru_cache(maxsize=1)
def _socket_count() -> int:
    """Physical CPU packages (sockets). Cached - static for the process lifetime."""
    try:
        r = _run(["powershell", "-NoProfile", "-Command",
                  "(Get-CimInstance Win32_ComputerSystem).NumberOfProcessors"])
        return int(r.stdout.strip())
    except Exception:
        return 1


def _cpu_pct() -> float:
    """System-wide Windows CPU utility.

    psutil occasionally reports bogus 100% samples from the long-running pythonw
    dashboard process on this dual-socket host. Windows' formatted processor
    utility counter matches Task Manager, so prefer it and keep psutil as a
    fallback for CLI/portable use.
    """
    try:
        r = _run(["powershell", "-NoProfile", "-Command",
                  "(Get-CimInstance Win32_PerfFormattedData_Counters_ProcessorInformation "
                  "-Filter \"Name='_Total'\").PercentProcessorUtility"])
        out = r.stdout.strip()
        if out:
            return round(float(out), 1)
    except Exception:
        pass
    if psutil:
        return round(psutil.cpu_percent(interval=0.3), 1)
    return 0.0


@functools.lru_cache(maxsize=1)
def wsl_ip() -> str:
    """The WSL VM's NAT IP (eth0). Cached; refreshes when the process restarts."""
    try:
        out = _run(WSL + ["hostname", "-I"]).stdout.strip()
        return out.split()[0] if out else ""
    except Exception:
        return ""


def system_info(extended: bool = False) -> dict:
    """Host + CPU + RAM + GPU snapshot (framework-agnostic; reusable by a web UI).
    extended=True adds the WSL VM IP (an extra wsl call; used by the web dashboard)."""
    info = {"host": platform.node(), "os": f"{platform.system()} {platform.release()}",
            "cpu": _cpu_name(), "gpus": gpu_status(), "sockets": _socket_count(),
            "lan_ip": os.environ.get("HOST_LAN_IP", "")}
    if psutil:
        info["cores"] = psutil.cpu_count(logical=True)        # logical (= threads); CLI uses this
        info["threads"] = psutil.cpu_count(logical=True)
        info["cores_phys"] = psutil.cpu_count(logical=False)  # physical cores
        info["cpu_pct"] = _cpu_pct()
        vm = psutil.virtual_memory()
        info["ram_used_gb"] = round(vm.used / 1e9, 1)
        info["ram_total_gb"] = round(vm.total / 1e9, 1)
        info["ram_pct"] = vm.percent
        info["uptime"] = _fmt_uptime(int(time.time() - psutil.boot_time()))
    if extended:
        info["wsl_ip"] = wsl_ip()
    return info


# neofetch-style Windows logo (classic 4-colour flag). set_colors 6 7 2 1 3 ->
# c1=cyan c2=white c3=green c4=red c5=yellow. All ASCII so it renders cleanly.
# orange theme: bright orange on top, darker orange on the bottom panes
_LOGO_COLORS = {"c1": "dark_orange3", "c2": "orange3", "c3": "orange1",
                "c4": "orange1", "c5": "dark_orange3"}
WIN_RAW = r"""${c3}                            .oodMMMM
                   .oodMMMMMMMMMMMMM
${c4}       ..oodMMM${c3}  MMMMMMMMMMMMMMMMMMM
${c4} oodMMMMMMMMMMM${c3}  MMMMMMMMMMMMMMMMMMM
${c4} MMMMMMMMMMMMMM${c3}  MMMMMMMMMMMMMMMMMMM
${c4} MMMMMMMMMMMMMM${c3}  MMMMMMMMMMMMMMMMMMM
${c4} MMMMMMMMMMMMMM${c3}  MMMMMMMMMMMMMMMMMMM
${c4} MMMMMMMMMMMMMM${c3}  MMMMMMMMMMMMMMMMMMM
${c4} MMMMMMMMMMMMMM${c3}  MMMMMMMMMMMMMMMMMMM

${c1} MMMMMMMMMMMMMM${c5}  MMMMMMMMMMMMMMMMMMM
${c1} MMMMMMMMMMMMMM${c5}  MMMMMMMMMMMMMMMMMMM
${c1} MMMMMMMMMMMMMM${c5}  MMMMMMMMMMMMMMMMMMM
${c1} MMMMMMMMMMMMMM${c5}  MMMMMMMMMMMMMMMMMMM
${c1} MMMMMMMMMMMMMM${c5}  MMMMMMMMMMMMMMMMMMM
${c1} `^^^^^^MMMMMMM${c5}  MMMMMMMMMMMMMMMMMMM
${c1}       ````^^^^${c5}  ^^MMMMMMMMMMMMMMMMM
                      ````^^^^^^MMMM"""


def render_logo(raw: str = WIN_RAW) -> str:
    """Convert neofetch ${cN} colour markers into rich markup, carrying colour across lines."""
    cur = None
    out = []
    for line in raw.splitlines():
        parts = re.split(r"\$\{(c\d)\}", line)
        buf = f"[{cur}]{parts[0]}[/{cur}]" if (cur and parts[0]) else parts[0]
        for i in range(1, len(parts), 2):
            cur = _LOGO_COLORS.get(parts[i], "white")
            txt = parts[i + 1] if i + 1 < len(parts) else ""
            if txt:
                buf += f"[{cur}]{txt}[/{cur}]"
        out.append(buf)
    return "\n".join(out)


def lms_ps() -> str:
    return _run(["lms", "ps"]).stdout.strip()


def lms_ps_json() -> list[dict]:
    """Loaded models as structured data (for the web UI). [] if lms/JSON unavailable."""
    try:
        return json.loads(_run(["lms", "ps", "--json"]).stdout)
    except Exception:
        return []


def containers_json() -> list[dict]:
    """Running containers as {name, status} dicts (for the web UI)."""
    raw = _run(WSL + ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"]).stdout.strip()
    out = []
    for line in raw.splitlines():
        if "\t" in line:
            name, status = line.split("\t", 1)
            out.append({"name": name, "status": status})
    return out


def lms_load(model: str, gpu: str = "max", ctx: Optional[int] = None,
             parallel: Optional[int] = None, ttl: Optional[int] = None) -> int:
    cmd = ["lms", "load", model, "--gpu", gpu, "-y"]
    if ctx:
        cmd += ["--context-length", str(ctx)]
    # LM Studio splits context-length across `parallel` slots, so a request only sees
    # ctx/parallel tokens. For a single-agent workload (coding) pass parallel=1 to hand
    # the whole window to the one active request.
    if parallel:
        cmd += ["--parallel", str(parallel)]
    if ttl:
        cmd += ["--ttl", str(ttl)]
    return _run(cmd, capture=False).returncode


def lms_unload(model: str) -> int:
    return _run(["lms", "unload", model], capture=False).returncode


def lms_unload_all() -> int:
    return _run(["lms", "unload", "--all"], capture=False).returncode


def compose(compose_file: Path, *args: str, capture: bool = False) -> subprocess.CompletedProcess:
    return _run(WSL + ["docker", "compose", "-f", _wsl_path(compose_file), *args], capture=capture)


def docker_ps() -> list[dict]:
    r = _run(WSL + ["docker", "ps", "--format", "{{json .}}"])
    out = []
    for line in r.stdout.strip().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def docker_stats() -> list[dict]:
    r = _run(WSL + ["docker", "stats", "--no-stream", "--format", "{{json .}}"])
    out = []
    for line in r.stdout.strip().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except Exception:
        return False


EXPECTED_CONTAINERS = ["open-webui", "litellm", "qdrant", "searxng", "jupyter"]


def health_checks() -> list[dict]:
    """Status of every service the stack needs (host services + core containers)."""
    running = {c.get("name", "") for c in containers_json()}
    checks = [
        {"name": "LM Studio :1234", "kind": "host", "ok": _port_open("localhost", 1234)},
        {"name": "mem0 :8077", "kind": "host", "ok": mem0_health() is not None},
        {"name": "web :8090", "kind": "host", "ok": web_health()},
    ]
    for c in EXPECTED_CONTAINERS:
        checks.append({"name": f"container {c}", "kind": "container", "ok": c in running})
    return checks


def _loaded_model_ids() -> set[str]:
    ids = set()
    for row in lms_ps_json():
        for key in ("model_key", "model", "identifier", "path", "name"):
            val = row.get(key)
            if val:
                ids.add(str(val))
    return ids


def _litellm_headers(key_name: str) -> dict:
    env = _load_env_file()
    key = env.get(key_name) or os.environ.get(key_name) or env.get("LITELLM_MASTER_KEY") or os.environ.get("LITELLM_MASTER_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _env_value(name: str) -> str:
    return os.environ.get(name) or _load_env_file().get(name, "")


def litellm_ready(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen("http://localhost:4000/health/readiness", timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def litellm_models(key_name: str = "LITELLM_KEY_CLINE") -> list[str]:
    try:
        res = _json_request("http://localhost:4000/v1/models", headers=_litellm_headers(key_name), timeout=5)
        return sorted(m.get("id", "") for m in res.get("data", []) if m.get("id"))
    except Exception:
        return []


def litellm_chat_smoke(model: str, key_name: str, prompt: str = "Reply with exactly: CODE_SMOKE_OK") -> tuple[bool, str]:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        # qwen3.6 is a reasoning model and can spend hundreds of tokens thinking
        # before producing visible content. A tiny cap makes a healthy stack look
        # broken, so keep the smoke short by prompt, not by starving output.
        "max_tokens": 4096,
    }
    try:
        res = _json_request("http://localhost:4000/v1/chat/completions", method="POST",
                            body=body, headers=_litellm_headers(key_name), timeout=90)
        msg = ((res.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return ("CODE_SMOKE_OK" in msg), msg.strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def mem0_recall(user_id: str, query: str, limit: int = 5) -> list[str]:
    body = {"user_id": user_id, "query": query, "limit": limit}
    headers = {}
    token = os.environ.get("MEMORI_SERVICE_TOKEN") or _load_env_file().get("MEMORI_SERVICE_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    res = _json_request(f"{MEM0_URL}/recall", method="POST", body=body, headers=headers, timeout=15)
    return res.get("memories", [])


def mem0_record(user_id: str, text: str) -> bool:
    body = {"user_id": user_id, "text": text}
    headers = {}
    token = os.environ.get("MEMORI_SERVICE_TOKEN") or _load_env_file().get("MEMORI_SERVICE_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    res = _json_request(f"{MEM0_URL}/record", method="POST", body=body, headers=headers, timeout=15)
    return bool(res.get("ok"))


def _meshtastic_headers() -> dict:
    token = os.environ.get("MESHTASTIC_MEM0_SERVICE_TOKEN") or _load_env_file().get("MESHTASTIC_MEM0_SERVICE_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def meshtastic_record(user_id: str, text: str) -> bool:
    body = {"user_id": user_id, "text": text}
    res = _json_request(f"{MESHTASTIC_MEM0_URL}/record", method="POST",
                        body=body, headers=_meshtastic_headers(), timeout=20)
    return bool(res.get("ok"))


def litellm_embed(text: str) -> list[float]:
    res = _json_request("http://localhost:4000/v1/embeddings", method="POST",
                        body={"model": "embed", "input": text},
                        headers=_litellm_headers("LITELLM_KEY_MEM0"), timeout=60)
    data = res.get("data") or []
    if not data or "embedding" not in data[0]:
        raise RuntimeError("LiteLLM embedding response did not include an embedding")
    return data[0]["embedding"]


def meshtastic_upsert_reference(user_id: str, source: str, chunk_index: int, text: str) -> bool:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    digest = hashlib.md5(f"{user_id}\n{source}\n{chunk_index}\n{text}".encode("utf-8")).hexdigest()
    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meshtastic:{digest}"))
    body = {
        "points": [{
            "id": point_id,
            "vector": litellm_embed(text),
            "payload": {
                "user_id": user_id,
                "data": text,
                "text_lemmatized": text,
                "hash": digest,
                "created_at": now,
                "updated_at": now,
                "attributed_to": "reference-import",
                "source_file": source,
                "chunk": chunk_index,
            },
        }],
    }
    res = _json_request(f"{QDRANT_URL}/collections/{MESHTASTIC_MEM0_COLLECTION}/points?wait=true",
                        method="PUT", body=body, timeout=90)
    return res.get("status") == "ok"


def _iter_markdown_files(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in {".md", ".markdown"} else []
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in path.glob(pattern)
                  if p.is_file() and p.suffix.lower() in {".md", ".markdown"})


def _chunk_markdown(text: str, max_chars: int) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: list[str] = []
    buf: list[str] = []
    for line in lines:
        if line.startswith("#") and buf:
            sections.append("\n".join(buf).strip())
            buf = []
        buf.append(line)
    if buf:
        sections.append("\n".join(buf).strip())

    chunks: list[str] = []
    current = ""
    for section in [s for s in sections if s.strip()]:
        if len(section) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            for i in range(0, len(section), max_chars):
                chunks.append(section[i:i + max_chars].strip())
            continue
        candidate = f"{current}\n\n{section}".strip() if current else section
        if len(candidate) > max_chars and current:
            chunks.append(current.strip())
            current = section
        else:
            current = candidate
    if current:
        chunks.append(current.strip())
    return chunks


def _qdrant_filter(user_id: str) -> dict:
    return {"must": [{"key": "user_id", "match": {"value": user_id}}]}


def qdrant_scroll(user_id: Optional[str] = None, limit: int = 256) -> list[dict]:
    points = []
    offset = None
    while True:
        body = {"limit": limit, "with_payload": True, "with_vector": False}
        if user_id:
            body["filter"] = _qdrant_filter(user_id)
        if offset is not None:
            body["offset"] = offset
        res = _json_request(f"{QDRANT_URL}/collections/mem0/points/scroll", method="POST", body=body, timeout=20)
        result = res.get("result") or {}
        points.extend(result.get("points") or [])
        offset = result.get("next_page_offset")
        if not offset:
            return points


def mem0_user_summary() -> list[dict]:
    rows: dict[str, dict] = {}
    for p in qdrant_scroll():
        payload = p.get("payload") or {}
        uid = payload.get("user_id") or "(none)"
        row = rows.setdefault(uid, {"user_id": uid, "count": 0, "last": ""})
        row["count"] += 1
        ts = payload.get("updated_at") or payload.get("created_at") or ""
        if ts > row["last"]:
            row["last"] = ts
    return sorted(rows.values(), key=lambda r: (-r["count"], r["user_id"]))


def mem0_delete_user(user_id: str) -> int:
    count = len(qdrant_scroll(user_id=user_id))
    if count == 0:
        return 0
    body = {"filter": _qdrant_filter(user_id)}
    _json_request(f"{QDRANT_URL}/collections/mem0/points/delete", method="POST", body=body, timeout=30)
    return count


def code_guardrails() -> dict:
    loaded = _loaded_model_ids()
    code_loaded = any("qwen/qwen3.6-35b-a3b" in m for m in loaded)
    chat_loaded = any("google/gemma-4-26b-a4b-qat" in m for m in loaded)
    image_loaded = any("google/gemma-4-12b-qat" in m for m in loaded)
    if code_loaded:
        profile = "code"
        safe = ["cline", "qwen-code", "claude-local-main", "claude-local-fast", "qwen/qwen3.6-35b-a3b"]
        warning = ""
    elif chat_loaded:
        profile = "chat"
        safe = ["google/gemma-4-26b-a4b-qat"]
        warning = "qwen aliases will JIT-load the coding model; run stack profile code first."
    elif image_loaded:
        profile = "image"
        safe = ["google/gemma-4-12b-qat"]
        warning = "qwen aliases will JIT-load the coding model; run stack profile code first."
    else:
        profile = "unknown"
        safe = []
        warning = "no known main profile model is loaded."
    return {"profile": profile, "loaded": sorted(loaded), "safe_aliases": safe, "warning": warning}


def code_project_init(path: Path, project: Optional[str], force: bool = False) -> list[Path]:
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    project_name = project or path.name
    written = []
    files = {
        path / ".clinerules": "\n".join([
            "# local coding stack",
            "",
            "- Use the OpenAI Compatible provider at http://localhost:4000/v1.",
            "- Use model cline for Cline work in this repo.",
            f"- Use MEMORI_PROJECT={project_name} for this repo's Mem0 namespace.",
            "- Recall memory at the start of work; record only durable project facts.",
            "- Run stack code doctor before debugging model, key, or memory failures.",
            "",
        ]),
        path / ".local-ai-stack.json": json.dumps({
            "provider": "openai-compatible",
            "base_url": "http://localhost:4000/v1",
            "cline_model": "cline",
            "qwen_model": "qwen-code",
            "memory_project": project_name,
            "recommended_profile": "code",
        }, indent=2) + "\n",
    }
    for f, content in files.items():
        if f.exists() and not force:
            continue
        f.write_text(content, encoding="utf-8")
        written.append(f)
    return written


def recover() -> list[str]:
    """Restart downed host services + bring up missing containers. Returns an action log.
    Shared by `stack doctor --fix` and the dashboard's recover button."""
    down = [c for c in health_checks() if not c["ok"]]
    log = []
    if any(c["kind"] == "container" for c in down):
        compose(MAIN_COMPOSE, "up", "-d")
        log.append("docker compose up -d (containers)")
    if any(c["name"].startswith("mem0") for c in down):
        mem0_stop()
        mem0_start()
        log.append("restarted mem0")
    if any(c["name"].startswith("web") for c in down):
        web_start()
        log.append("started web dashboard")
    if any("LM Studio" in c["name"] for c in down):
        log.append("LM Studio is a desktop app - start it manually")
    return log


def container_rows() -> list[dict]:
    """Running containers plus one-shot resource usage from docker stats."""
    stats = {row.get("Name"): row for row in docker_stats()}
    rows = []
    for row in docker_ps():
        name = row.get("Names") or row.get("Name") or ""
        stat = stats.get(name, {})
        rows.append({
            "name": name,
            "image": row.get("Image", ""),
            "status": row.get("Status", ""),
            "cpu": stat.get("CPUPerc", ""),
            "mem": stat.get("MemUsage", "").split(" / ")[0],
            "mem_pct": stat.get("MemPerc", ""),
            "net": stat.get("NetIO", ""),
            "block": stat.get("BlockIO", ""),
            "pids": stat.get("PIDs", ""),
        })
    return rows


def render_container_table(rows: list[dict]) -> Table:
    table = Table(show_header=True, header_style="bold orange1", box=None, pad_edge=False)
    table.add_column("container", style="orange3", no_wrap=True)
    table.add_column("status", style="dark_orange3", no_wrap=True)
    table.add_column("cpu", justify="right")
    table.add_column("mem used", justify="right")
    table.add_column("mem%", justify="right")
    table.add_column("pids", justify="right")
    for row in rows:
        table.add_row(row["name"], row["status"], row["cpu"], row["mem"],
                      row["mem_pct"], row["pids"])
    return table


# ---------------------------------------------------------------- Mem0 memory service
# Runs on the HOST (windowless venv pythonw), not in Docker - manage it via processes.
def mem0_health(timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"{MEM0_URL}/health", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def mem0_procs() -> list:
    """All processes running mem0_service.py. NOTE: the venv pythonw.exe is a launcher
    stub that spawns the base interpreter as a child, so ONE logical service shows up
    as a parent+child pair here. Use mem0_instances() for the logical count."""
    if not psutil:
        return []
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if "mem0_service.py" in " ".join(p.info["cmdline"] or []):
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def mem0_instances() -> int:
    """Logical service count: processes whose parent isn't itself a mem0 process."""
    procs = mem0_procs()
    pids = {p.pid for p in procs}
    n = 0
    for p in procs:
        try:
            if p.ppid() not in pids:
                n += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return n


def mem0_start(wait_secs: float = 30.0) -> bool:
    """Start the service under the venv pythonw; True once /health responds."""
    if mem0_health():
        return True
    subprocess.Popen([str(MEM0_PYW), str(MEM0_SCRIPT)], cwd=str(MEM0_SCRIPT.parent),
                     creationflags=subprocess.DETACHED_PROCESS
                     | subprocess.CREATE_NEW_PROCESS_GROUP,
                     startupinfo=_hidden_subprocess_kwargs().get("startupinfo"))
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        time.sleep(0.5)
        if mem0_health():
            return True
    return False


def mem0_stop() -> int:
    """Terminate every mem0_service.py process (incl. strays). Returns count killed."""
    procs = mem0_procs()
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(procs, timeout=5)
    return len(procs)


def _script_procs(script_name: str) -> list:
    if not psutil:
        return []
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if script_name in " ".join(p.info["cmdline"] or []):
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def _start_windowless(script: Path, cwd: Path) -> None:
    subprocess.Popen([str(MEM0_PYW), str(script)], cwd=str(cwd),
                     creationflags=subprocess.DETACHED_PROCESS
                     | subprocess.CREATE_NEW_PROCESS_GROUP,
                     startupinfo=_hidden_subprocess_kwargs().get("startupinfo"))


def meshtastic_mem0_health(timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"{MESHTASTIC_MEM0_URL}/health", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def meshtastic_mcp_health(timeout: float = 2.0) -> bool:
    return _port_open("localhost", MESHTASTIC_MCP_PORT, timeout=timeout)


def _listening_pid(port: int) -> Optional[int]:
    if not psutil:
        return None
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port:
                return c.pid
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return None
    return None


def meshtastic_procs() -> list:
    return _script_procs("meshtastic_mem0_service.py") + _script_procs("meshtastic_memory_mcp.py")


def meshtastic_start(wait_secs: float = 45.0) -> bool:
    if not meshtastic_mem0_health():
        _start_windowless(MESHTASTIC_MEM0_SCRIPT, MESHTASTIC_MEM0_SCRIPT.parent)
    if not meshtastic_mcp_health():
        _start_windowless(MESHTASTIC_MEMORY_MCP_SCRIPT, MESHTASTIC_MEMORY_MCP_SCRIPT.parent)
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        time.sleep(0.5)
        if meshtastic_mem0_health() and meshtastic_mcp_health():
            return True
    return bool(meshtastic_mem0_health() and meshtastic_mcp_health())


def meshtastic_stop() -> int:
    procs = meshtastic_procs()
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(procs, timeout=5)
    return len(procs)


# ---------------------------------------------------------------- web dashboard (host)
# Same venv pythonw + background-process pattern as mem0; serves the stackctl UI on :8090.
def web_health(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{WEB_URL}/api/health", timeout=timeout) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception:
        return False


def web_procs() -> list:
    if not psutil:
        return []
    out = []
    seen = set()
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if "webapp.py" in " ".join(p.info["cmdline"] or []):
                out.append(p)
                seen.add(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # pythonw.exe can hide its command line; fall back to the owner of the
    # dashboard listen socket so `stack web restart` can kill the real process.
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == 8090 and c.pid and c.pid not in seen:
                out.append(psutil.Process(c.pid))
                seen.add(c.pid)
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    return out


def web_start(wait_secs: float = 20.0) -> bool:
    if web_health():
        return True
    subprocess.Popen([str(MEM0_PYW), str(WEB_SCRIPT)], cwd=str(WEB_SCRIPT.parent),
                     creationflags=subprocess.DETACHED_PROCESS
                     | subprocess.CREATE_NEW_PROCESS_GROUP,
                     startupinfo=_hidden_subprocess_kwargs().get("startupinfo"))
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        time.sleep(0.5)
        if web_health():
            return True
    return False


def web_stop() -> int:
    procs = web_procs()
    killed = 0
    for p in procs:
        try:
            p.terminate()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Scheduled-task-launched pythonw.exe can run elevated/session-0 and
            # deny terminate from a normal shell. Try taskkill, then report what
            # remains via the port-release wait below.
            try:
                r = _run(["taskkill", "/F", "/PID", str(p.pid)])
                if r.returncode == 0:
                    killed += 1
            except Exception:
                pass
    try:
        psutil.wait_procs(procs, timeout=5)
    except psutil.AccessDenied:
        pass
    # Wait for the port to actually release, so an immediate restart can rebind it
    # (Windows can hold the socket briefly after the process dies -> bind races).
    for _ in range(24):
        if not _port_open("127.0.0.1", 8090, 0.3):
            break
        time.sleep(0.25)
    return killed


# ---------------------------------------------------------------- Kimi llama-server (host)
# Kimi-K2.7-Code runs as a standalone llama.cpp server on the HOST, CPU+RAM only (the 339GB
# MoE doesn't fit VRAM). NOT an LM Studio model and NOT a GPU profile, so it's managed here
# like mem0/web: a background host process. The LiteLLM container reaches it via the LAN IP +
# portproxy HOST_LAN_IP:8095 (firewall rule "Kimi llama-server 8095"), same pattern as
# LM Studio:1234 / Mem0:8077. Loading the 339GB into RAM takes ~4-5 min; gen is ~3.3 tok/s.
def kimi_health(timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"{KIMI_URL}/health", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def kimi_procs() -> list:
    """The standalone llama-server process serving Kimi on :8095.

    Windows also has an IP Helper svchost listening on the LAN portproxy for 8095.
    Do not count that socket owner as Kimi; only a real llama-server process should
    show up here or be stopped.
    """
    if not psutil:
        return []
    out, seen = [], set()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info.get("name") or "").lower()
            cl = " ".join(p.info["cmdline"] or []).lower()
            if "llama-server" in name and (str(KIMI_PORT) in cl or KIMI_MODEL.name.lower() in cl):
                out.append(p)
                seen.add(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def kimi_proxy_health(timeout: float = 1.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"{KIMI_PROXY_URL}/health", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def kimi_proxy_procs() -> list:
    if not psutil:
        return []
    out = []
    needle = str(KIMI_PROXY_SCRIPT).lower()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info["cmdline"] or []).lower()
            if "kimi_lazy_proxy.py" in cl or needle in cl:
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def kimi_proxy_start(wait_secs: float = 10.0) -> bool:
    if kimi_proxy_health():
        return True
    KIMI_PROXY_LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = open(KIMI_PROXY_LOG, "ab")  # noqa: SIM115 - handed to detached child
    subprocess.Popen(
        [str(MEM0_PYW), str(KIMI_PROXY_SCRIPT)],
        stdout=logf,
        stderr=logf,
        cwd=str(ROOT),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        startupinfo=_hidden_subprocess_kwargs().get("startupinfo"),
    )
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        time.sleep(0.5)
        if kimi_proxy_health():
            return True
    return False


def kimi_proxy_stop() -> int:
    procs = kimi_proxy_procs()
    killed = 0
    for p in procs:
        try:
            p.terminate()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            try:
                if _run(["taskkill", "/F", "/PID", str(p.pid)]).returncode == 0:
                    killed += 1
            except Exception:
                pass
    try:
        psutil.wait_procs(procs, timeout=5)
    except Exception:
        pass
    for p in kimi_proxy_procs():
        try:
            if _run(["taskkill", "/F", "/PID", str(p.pid)]).returncode == 0:
                killed += 1
        except Exception:
            pass
    return killed


def kimi_start(wait_secs: float = 420.0) -> bool:
    """Launch llama-server with Kimi (CPU+RAM, NUMA-distributed). True once /health is ok.
    Loading the 339GB into RAM takes ~4-5 min, hence the long default wait. --no-mmap forces
    full RAM residency. The launch flags mirror the box-tested command in STATE.md."""
    if kimi_health():
        return True
    if not (KIMI_SERVER.exists() and KIMI_MODEL.exists()):
        return False
    cmd = [str(KIMI_SERVER), "-m", str(KIMI_MODEL), "--numa", "distribute",
           "-t", str(KIMI_THREADS)]
    if KIMI_CPU_RANGE:  # span both sockets/processor groups (see KIMI_CPU_RANGE note above)
        cmd += ["--cpu-range", KIMI_CPU_RANGE, "--cpu-strict", "1"]
    cmd += ["-c", str(KIMI_CTX), "--host", "127.0.0.1", "--port", str(KIMI_PORT), "--no-mmap"]
    KIMI_LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = open(KIMI_LOG, "ab")  # noqa: SIM115 - handed to the detached child
    subprocess.Popen(cmd, stdout=logf, stderr=logf, cwd=str(KIMI_SERVER.parent),
                     creationflags=subprocess.DETACHED_PROCESS
                     | subprocess.CREATE_NEW_PROCESS_GROUP,
                     startupinfo=_hidden_subprocess_kwargs().get("startupinfo"))
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        time.sleep(2)
        if kimi_health():
            return True
    return False


def kimi_stop() -> int:
    """Terminate the Kimi llama-server, freeing ~305GB RAM. Returns count killed."""
    procs = kimi_procs()
    killed = 0
    for p in procs:
        try:
            p.terminate()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            try:
                if _run(["taskkill", "/F", "/PID", str(p.pid)]).returncode == 0:
                    killed += 1
            except Exception:
                pass
    try:
        psutil.wait_procs(procs, timeout=10)
    except psutil.AccessDenied:
        pass
    for _ in range(20):
        if not kimi_procs():
            break
        time.sleep(0.25)
    return killed


# ---------------------------------------------------------- LM Studio scheduler proxy
def lm_scheduler_health(timeout: float = 5.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"{LM_SCHEDULER_URL}/health", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def lm_scheduler_procs() -> list:
    if not psutil:
        return []
    out = []
    needle = str(LM_SCHEDULER_SCRIPT).lower()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info["cmdline"] or []).lower()
            if "lmstudio_scheduler_proxy.py" in cl or needle in cl:
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def lm_scheduler_start(wait_secs: float = 10.0) -> bool:
    if lm_scheduler_health():
        return True
    LM_SCHEDULER_LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = open(LM_SCHEDULER_LOG, "ab")  # noqa: SIM115 - handed to detached child
    subprocess.Popen(
        [str(MEM0_PYW), str(LM_SCHEDULER_SCRIPT)],
        stdout=logf,
        stderr=logf,
        cwd=str(ROOT),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        startupinfo=_hidden_subprocess_kwargs().get("startupinfo"),
    )
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        time.sleep(0.5)
        if lm_scheduler_health():
            return True
    return False


def lm_scheduler_stop() -> int:
    procs = lm_scheduler_procs()
    killed = 0
    for p in procs:
        try:
            p.terminate()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            try:
                if _run(["taskkill", "/F", "/PID", str(p.pid)]).returncode == 0:
                    killed += 1
            except Exception:
                pass
    try:
        psutil.wait_procs(procs, timeout=5)
    except Exception:
        pass
    for p in lm_scheduler_procs():
        try:
            if _run(["taskkill", "/F", "/PID", str(p.pid)]).returncode == 0:
                killed += 1
        except Exception:
            pass
    return killed


# ================================================================ PROFILES
# A workload = which models on the GPU + which containers running. Applying a profile
# does the VRAM juggling for you (unload conflicting models before GPU-heavy work).
# The "memory core": a small dedicated Mem0 brain (qwen2.5-1.5b-instruct, ~1.65GB) + the embedder.
# Kept resident in every profile so Mem0 keeps recording/recalling no matter which main chat
# model is loaded - swap the main freely (qwen, a bigger gemma, ...) and memory just works.
# Brain history: was gemma-4-e4b (6.3GB), but that couldn't co-reside with qwen-35b in the `code`
# profile (only ~5GB VRAM free there), so memory writes silently failed in code. qwen2.5-1.5b is
# tiny enough to fit alongside qwen-35b AND non-reasoning, so it emits clean JSON for Mem0 (no
# thinking tokens to strip) and extracts in ~1s on GPU. brain ctx kept small (8192) to save VRAM;
# memory extraction inputs are short. (LiteLLM's `brain` alias points here; see litellm/config.yaml.)
MEMORY_CORE = [("qwen2.5-1.5b-instruct", "max", 8192, 1),  # parallel=1 -> full 8192 ctx per extraction
               ("text-embedding-nomic-embed-text-v1.5", "max", None)]

# Hermes delegation gets its own small CPU model so sub-agents don't queue behind the
# coding qwen-35b, which is loaded with parallel=1 for the full context window.
HERMES_SUBAGENT = [("qwen2.5-3b-instruct", "off", 65536, 16)]

PROFILES: dict[str, dict] = {
    "chat": {
        "desc": "Daily driver - main model (gemma-26b) + memory core + web stack.",
        "unload_all_first": True,
        "load": [("google/gemma-4-26b-a4b-qat", "max", 32768, None, LM_SCHEDULER_IDLE_SECONDS)] + MEMORY_CORE,
        "pre_compose": [(MINERU_GPU_COMPOSE, ["down"]),             # free GPU from gpu-mineru
                        (MAIN_COMPOSE, ["stop", "vllm", "comfyui"])],
        "post_compose": [(MAIN_COMPOSE, ["up", "-d", "litellm", "qdrant", "open-webui", "searxng", "jupyter"])],
    },
    "code": {
        "desc": "Coding agents - qwen3.6-35b-a3b @ 320k + memory core + Hermes sub-agent model.",
        # qwen-35b-a3b (~22GB split across both cards) is the one local model that reliably emits
        # valid tool-call args, so claude-local stops throwing "Invalid tool parameters" and Cline
        # writes files cleanly. The litellm claude-local-* aliases point here (see litellm/config.yaml).
        # CONTEXT: loaded at 327680 with parallel=1 so the single coding agent gets the FULL 320k.
        # (LM Studio divides context-length across parallel slots; the old parallel=4 default meant a
        # request only saw ctx/4. The GGUF metadata says maxContextLength=262144, but LM Studio loads
        # 327680 cleanly on this box. It leaves little free VRAM on GPU0.)
        # MEMORY: earlier this profile dropped the memory core because gemma-4-e4b (6.3GB) couldn't
        # co-reside with qwen-35b, so Mem0 writes silently failed in code. The brain is now the tiny
        # qwen2.5-1.5b (~1.65GB, see MEMORY_CORE), which DOES fit the ~5GB headroom, so memory works
        # here too. If qwen-35b + brain ever OOM GPU0, drop qwen ctx to 262144 to free room.
        # HERMES: qwen2.5-3b is CPU-only at parallel=16 so delegate_task children can run
        # concurrently without stealing GPU from the coding model.
        # NOTE: qwen3.6 is a *reasoning* model - it "thinks" first, so give agents token headroom.
        "unload_all_first": True,
        "load": [("qwen/qwen3.6-35b-a3b", "max", 327680, 1, LM_SCHEDULER_IDLE_SECONDS)] + MEMORY_CORE + HERMES_SUBAGENT,
        "pre_compose": [(MINERU_GPU_COMPOSE, ["down"]),            # free GPU from gpu-mineru
                        (MAIN_COMPOSE, ["stop", "vllm", "comfyui"])],
        "post_compose": [(MAIN_COMPOSE, ["up", "-d", "litellm", "qdrant", "searxng"])],
    },
    "extract": {
        "desc": "MinerU on CPU - coexists with chat models (background extraction).",
        "unload_all_first": False,
        "load": [],
        "pre_compose": [(MINERU_GPU_COMPOSE, ["down"])],            # gpu variant uses same ports
        "post_compose": [(MINERU_COMPOSE, ["up", "-d"])],
    },
    "extract-gpu": {
        "desc": "MinerU on GPU; unloads the main chat model, keeps the memory core.",
        "unload_all_first": True,
        "load": MEMORY_CORE,
        "pre_compose": [(MAIN_COMPOSE, ["stop", "vllm", "comfyui"]),  # free GPU from vllm/comfyui
                        (MINERU_COMPOSE, ["down"])],                # stop CPU variant (same ports)
        "post_compose": [(MINERU_GPU_COMPOSE, ["up", "-d"])],
    },
    "vllm": {
        "desc": "Free the GPU for vLLM serving; unloads the main chat model, keeps the memory core.",
        "unload_all_first": True,
        "load": MEMORY_CORE,
        "pre_compose": [(MINERU_GPU_COMPOSE, ["down"]),
                        (MINERU_COMPOSE, ["down"]),
                        (MAIN_COMPOSE, ["stop", "comfyui"])],
        "post_compose": [(MAIN_COMPOSE, ["--profile", "vllm", "up", "-d", "vllm"])],
    },
    "image": {
        "desc": "Image generation (ComfyUI / Flux.1-dev fp8) + gemma-4-12b chat, so OpenWebUI's generate-image flow works.",
        # ComfyUI is pinned to GPU1 (CUDA_VISIBLE_DEVICES=1 in docker-compose.yml) so Flux gets a whole
        # card. The chat model that writes/refines image prompts in OpenWebUI is google/gemma-4-12b-qat,
        # picked by name there (it's a real litellm model_name, no aliasing). A 12B does NOT fully fit
        # beside Flux on a 16GB card (LM Studio splits it across both GPUs and the GPU1 half overflows
        # Flux), so it loads with PARTIAL GPU offload (`--gpu 0.45`) and a small context: ~3GB on each
        # card, the rest on the 512GB system RAM. Result: a 1024x1024/20-step image in ~70s while gemma
        # serves chat at ~15 tok/s. Trade-off: gemma-4 is a reasoning model, so it "thinks" before
        # answering and partial offload makes it slowish. For full-speed big-model chat use `chat`/`code`;
        # for the fastest prompt-writing, a small non-reasoning instruct model would be better.
        "unload_all_first": True,
        "load": [("google/gemma-4-12b-qat", "0.45", 4096),
                 ("text-embedding-nomic-embed-text-v1.5", "max", None)],
        "pre_compose": [(MINERU_GPU_COMPOSE, ["down"]),
                        (MAIN_COMPOSE, ["stop", "vllm"])],
        "post_compose": [(MAIN_COMPOSE, ["--profile", "image", "up", "-d", "comfyui"]),
                         (MAIN_COMPOSE, ["up", "-d", "open-webui", "litellm", "qdrant", "searxng"])],
    },
}


def _run_compose_steps(steps: list[tuple[Path, list[str]]]) -> None:
    for cf, args in steps:
        console.print(f"[cyan]compose[/] {cf.name}: {' '.join(args)}")
        compose(cf, *args)


def apply_profile(name: str) -> None:
    p = PROFILES[name]
    _run_compose_steps(p.get("pre_compose", []))
    if p["unload_all_first"]:
        console.print("[yellow]unloading all LM Studio models...[/]")
        lms_unload_all()
    for entry in p["load"]:
        model, gpu, ctx = entry[0], entry[1], entry[2]
        parallel = entry[3] if len(entry) > 3 else None
        ttl = entry[4] if len(entry) > 4 else None
        pstr = f", parallel={parallel}" if parallel else ""
        tstr = f", ttl={ttl}s" if ttl else ""
        console.print(f"[green]loading[/] {model} (gpu={gpu}, ctx={ctx}{pstr}{tstr})")
        lms_load(model, gpu, ctx, parallel, ttl)
    _run_compose_steps(p.get("post_compose", p.get("compose", [])))


# ================================================================ CLI
app = typer.Typer(add_completion=False, help="Manage the local AI stack by workload.")
model_app = typer.Typer(help="LM Studio model ops.")
app.add_typer(model_app, name="model")
mem0_app = typer.Typer(help="Mem0 memory service (host process on :8077).")
app.add_typer(mem0_app, name="mem0")
meshtastic_app = typer.Typer(help="Meshtastic Hermes memory services.")
app.add_typer(meshtastic_app, name="meshtastic")
mineru_app = typer.Typer(help="MinerU extraction jobs.")
app.add_typer(mineru_app, name="mineru")
openwebui_app = typer.Typer(help="OpenWebUI import/admin helpers.")
app.add_typer(openwebui_app, name="openwebui")
skillsusa_app = typer.Typer(help="SkillsUSA document helpers.")
app.add_typer(skillsusa_app, name="skillsusa")
web_app = typer.Typer(help="Web dashboard (host process on :8090).")
app.add_typer(web_app, name="web")
code_app = typer.Typer(help="Coding-agent checks, smoke tests, and project bootstrap.")
app.add_typer(code_app, name="code")
kimi_app = typer.Typer(help="Kimi-K2.7-Code lazy proxy and llama.cpp backend.")
app.add_typer(kimi_app, name="kimi")
lm_scheduler_app = typer.Typer(help="LM Studio chat/code profile scheduler proxy.")
app.add_typer(lm_scheduler_app, name="lm-scheduler")


@app.command()
def status():
    """neofetch-style system snapshot (CPU/RAM/GPU) + loaded models + containers."""
    info = system_info()
    logo_block = render_logo()

    def kv(k, v):
        return f"[bold orange1]{k:<6}[/][orange3]{v}[/]"

    sock = info.get("sockets", 1)
    cpu_label = (f"{sock}x " if sock > 1 else "") + info["cpu"]
    lines = [f"[bold orange1]{info['host']}[/]", "[dark_orange3]" + "-" * 28 + "[/]",
             kv("os", info["os"]), kv("cpu", cpu_label)]
    if psutil:
        lines.append(kv("", f"{info.get('cores_phys', '?')} cores / {info['threads']} threads "
                            f"@ {info['cpu_pct']}%"))
        lines.append(kv("ram", f"{info['ram_used_gb']} / {info['ram_total_gb']} GB "
                               f"({info['ram_pct']}%)"))
    for g in info["gpus"]:
        lines.append(kv(f"gpu{g['gpu']}",
                        f"{g['used_mb']} / {g['total_mb']} MB ({g['util_pct']}%)"))
    if psutil:
        lines.append(kv("up", info["uptime"]))

    grid = Table.grid(padding=(0, 3))
    grid.add_column()
    grid.add_column()
    grid.add_row(logo_block, "\n".join(lines))
    console.print(grid)
    console.print("\n[bold orange1]LM Studio models[/]\n" + (lms_ps() or "(none)"))
    rows = container_rows()
    console.print("\n[bold orange1]Containers[/]")
    console.print(render_container_table(rows) if rows else "(none)")
    h = mem0_health()
    n = mem0_instances()
    state = "[green]up[/]" if h else "[red]down[/]"
    extra = f"  [yellow]({n} instances - expected 1)[/]" if n > 1 else ""
    console.print(f"\n[bold orange1]Host services[/]\nmem0-memory\t:8077 {state}{extra}")
    mh = meshtastic_mem0_health()
    if mh or meshtastic_mcp_health() or meshtastic_procs():
        mstate = "[green]up[/]" if mh else "[red]down[/]"
        cstate = "[green]up[/]" if meshtastic_mcp_health() else "[red]down[/]"
        console.print(f"mesh-mem0\t:{MESHTASTIC_MEM0_PORT} {mstate}")
        console.print(f"mesh-mcp\t:{MESHTASTIC_MCP_PORT} {cstate}")
    sh = lm_scheduler_health()
    if sh or lm_scheduler_procs():
        sstate = "[green]up[/]" if sh else "[red]down[/]"
        console.print(f"lm-scheduler\t:{LM_SCHEDULER_PORT} {sstate}")
    ph = kimi_proxy_health()
    if ph or kimi_proxy_procs() or kimi_health() or kimi_procs():
        pstate = "[green]up[/]" if ph else "[red]down[/]"
        bstate = "[green]up[/]" if kimi_health() else "[yellow]loading[/]" if kimi_procs() else "[red]down[/]"
        console.print(f"kimi-proxy\t:{KIMI_PROXY_PORT} {pstate}")
        console.print(f"kimi-backend\t:{KIMI_BACKEND_PORT} {bstate}")


@model_app.command("ps")
def model_ps():
    """List loaded models."""
    console.print(lms_ps() or "(none)")


@model_app.command("load")
def model_load(model: str, ctx: int = typer.Option(None, help="context length"),
               gpu: str = typer.Option("max", help="gpu offload: max | off | 0.0-1.0"),
               parallel: int = typer.Option(None, help="parallel slots; context-length is split "
                                            "across them, so use 1 to give one request the full ctx")):
    """Load a model (full GPU offload by default)."""
    apply = lms_load(model, gpu, ctx, parallel)
    raise typer.Exit(apply)


@model_app.command("unload")
def model_unload(model: str = typer.Argument(None, help="model id, or omit for ALL")):
    """Unload one model, or all if no id given."""
    raise typer.Exit(lms_unload(model) if model else lms_unload_all())


@mem0_app.command("status")
def mem0_status():
    """Health + process check for the memory service."""
    h = mem0_health()
    console.print(f"health: {'[green]up[/] ' + str(h) if h else '[red]down[/]'}")
    console.print(f"instances: {mem0_instances()} (each = a launcher-stub + interpreter pair)")
    for p in mem0_procs():
        console.print(f"pid {p.pid}: {' '.join(p.info['cmdline'] or [])}")


@mem0_app.command("start")
def mem0_start_cmd():
    """Start the memory service (windowless, under the memori venv)."""
    if mem0_health():
        console.print("[green]already up.[/]")
        return
    ok = mem0_start()
    console.print("[bold green]mem0 up.[/]" if ok
                  else f"[red]did not come up - check {MEM0_LOG}[/]")
    raise typer.Exit(0 if ok else 1)


@mem0_app.command("stop")
def mem0_stop_cmd():
    """Stop the memory service (kills strays too)."""
    console.print(f"killed {mem0_stop()} process(es).")


@mem0_app.command("restart")
def mem0_restart():
    """Stop (incl. strays) then start fresh under the venv."""
    console.print(f"killed {mem0_stop()} process(es).")
    ok = mem0_start()
    console.print("[bold green]mem0 up.[/]" if ok
                  else f"[red]did not come up - check {MEM0_LOG}[/]")
    raise typer.Exit(0 if ok else 1)


@mem0_app.command("logs")
def mem0_logs(lines: int = typer.Option(30, "--lines", "-n", help="tail this many lines")):
    """Tail the service log."""
    if not MEM0_LOG.exists():
        console.print("(no log file yet)")
        return
    text = MEM0_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    console.print("\n".join(text[-lines:]) or "(empty)")


@mem0_app.command("users")
def mem0_users():
    """List Mem0 user/project ids in Qdrant."""
    rows = mem0_user_summary()
    table = Table(show_header=True, header_style="bold orange1", box=None, pad_edge=False)
    table.add_column("user_id", style="orange3")
    table.add_column("memories", justify="right")
    table.add_column("last write")
    for row in rows:
        table.add_row(row["user_id"], str(row["count"]), row["last"])
    console.print(table if rows else "(no memories)")


@mem0_app.command("list")
def mem0_list(
    user_id: str = typer.Argument(..., help="Mem0 user/project id, e.g. cline:myrepo"),
    limit: int = typer.Option(20, "--limit", "-n", help="number of recent memories to show"),
):
    """Show recent memories for one user/project id."""
    points = qdrant_scroll(user_id=user_id)
    points.sort(key=lambda p: (p.get("payload") or {}).get("updated_at") or "", reverse=True)
    for p in points[:limit]:
        payload = p.get("payload") or {}
        ts = payload.get("updated_at") or payload.get("created_at") or ""
        text = payload.get("data") or payload.get("memory") or ""
        console.print(f"[orange3]{ts}[/] [dim]{p.get('id')}[/]\n{text}\n")
    if not points:
        console.print("(no memories)")


@mem0_app.command("export")
def mem0_export(
    user_id: str = typer.Argument(..., help="Mem0 user/project id, e.g. cline:myrepo"),
    output: Path = typer.Option(None, "--output", "-o", help="JSON output path"),
):
    """Export one user/project's memories to JSON."""
    points = qdrant_scroll(user_id=user_id)
    points.sort(key=lambda p: (p.get("payload") or {}).get("updated_at") or "")
    if output is None:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", user_id).strip("_") or "mem0"
        output = ROOT / "backups" / f"mem0-{safe}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(points, indent=2), encoding="utf-8")
    console.print(f"[green]exported {len(points)} memories to {output}[/]")


@mem0_app.command("delete")
def mem0_delete(
    user_id: str = typer.Argument(..., help="Mem0 user/project id to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="confirm deletion"),
):
    """Delete every memory for one user/project id."""
    count = len(qdrant_scroll(user_id=user_id))
    if count == 0:
        console.print("(no memories)")
        return
    if not yes:
        console.print(f"[yellow]{count} memories match {user_id!r}. Re-run with --yes to delete.[/]")
        raise typer.Exit(1)
    deleted = mem0_delete_user(user_id)
    console.print(f"[green]delete requested for {deleted} memories.[/]")


@meshtastic_app.command("status")
def meshtastic_status():
    """Health and process check for Meshtastic memory services."""
    mh = meshtastic_mem0_health()
    ch = meshtastic_mcp_health()
    console.print(f"mem0 :{MESHTASTIC_MEM0_PORT}: {'[green]up[/] ' + str(mh) if mh else '[red]down[/]'}")
    console.print(f"mcp  :{MESHTASTIC_MCP_PORT}: {'[green]up[/]' if ch else '[red]down[/]'}")
    listener_pids = {pid for pid in (_listening_pid(MESHTASTIC_MEM0_PORT), _listening_pid(MESHTASTIC_MCP_PORT)) if pid}
    for pid in sorted(listener_pids):
        try:
            p = psutil.Process(pid)
            console.print(f"listener pid {p.pid}: {' '.join(p.cmdline())}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    helpers = [p for p in meshtastic_procs() if p.pid not in listener_pids]
    for p in helpers:
        try:
            console.print(f"helper pid {p.pid}: {' '.join(p.cmdline())}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


@meshtastic_app.command("start")
def meshtastic_start_cmd():
    """Start the Meshtastic Mem0 service and MCP bridge."""
    ok = meshtastic_start()
    console.print("[bold green]meshtastic memory up.[/]" if ok
                  else f"[red]did not come up - check {MESHTASTIC_MEM0_LOG} and {MESHTASTIC_MCP_LOG}[/]")
    raise typer.Exit(0 if ok else 1)


@meshtastic_app.command("stop")
def meshtastic_stop_cmd():
    """Stop the Meshtastic Mem0 service and MCP bridge."""
    console.print(f"killed {meshtastic_stop()} process(es).")


@meshtastic_app.command("restart")
def meshtastic_restart_cmd():
    """Restart the Meshtastic Mem0 service and MCP bridge."""
    console.print(f"killed {meshtastic_stop()} process(es).")
    ok = meshtastic_start()
    console.print("[bold green]meshtastic memory up.[/]" if ok
                  else f"[red]did not come up - check {MESHTASTIC_MEM0_LOG} and {MESHTASTIC_MCP_LOG}[/]")
    raise typer.Exit(0 if ok else 1)


@meshtastic_app.command("logs")
def meshtastic_logs(lines: int = typer.Option(30, "--lines", "-n", help="tail this many lines")):
    """Tail Meshtastic memory logs."""
    for path in (MESHTASTIC_MEM0_LOG, MESHTASTIC_MCP_LOG):
        console.print(f"[bold orange1]{path.name}[/]")
        if not path.exists():
            console.print("(no log file yet)")
            continue
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        console.print("\n".join(text[-lines:]) or "(empty)")


@meshtastic_app.command("import-md")
def meshtastic_import_md(
    path: Path = typer.Argument(..., exists=True, help="Markdown file or folder to import"),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="scan subfolders"),
    user_id: str = typer.Option(None, "--user-id", help="Mem0 user id"),
    chunk_chars: int = typer.Option(2500, "--chunk-chars", help="maximum characters per memory record"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show import plan without writing"),
    delay: float = typer.Option(0.25, "--delay", help="seconds to wait between writes"),
    via_mem0: bool = typer.Option(False, "--via-mem0", help="send chunks through Mem0 extraction instead of direct reference embedding"),
):
    """Import markdown files into the separate Meshtastic Mem0 collection."""
    user_id = user_id or os.environ.get("MESHTASTIC_MEMORY_USER_ID") or _load_env_file().get(
        "MESHTASTIC_MEMORY_USER_ID", "hermes:meshtastic")
    files = _iter_markdown_files(path, recursive=recursive)
    if not files:
        console.print("[yellow]no markdown files found.[/]")
        return
    if chunk_chars < 500:
        console.print("[red]--chunk-chars must be at least 500.[/]")
        raise typer.Exit(1)
    if not dry_run and via_mem0 and not meshtastic_mem0_health():
        console.print("[yellow]Meshtastic memory is not up; starting it now.[/]")
        if not meshtastic_start():
            console.print(f"[red]did not come up - check {MESHTASTIC_MEM0_LOG} and {MESHTASTIC_MCP_LOG}[/]")
            raise typer.Exit(1)

    plan: list[tuple[Path, str, int, str]] = []
    for file in files:
        text = file.read_text(encoding="utf-8-sig", errors="replace").strip()
        if not text:
            continue
        rel = file.relative_to(path if path.is_dir() else file.parent) if path.is_dir() else file.name
        for idx, chunk in enumerate(_chunk_markdown(text, chunk_chars), start=1):
            record = (
                f"Meshtastic reference import\n"
                f"Source file: {rel}\n"
                f"Chunk: {idx}\n\n"
                f"{chunk}"
            )
            plan.append((file, str(rel), idx, record))

    console.print(f"files: {len(files)}")
    console.print(f"records: {len(plan)}")
    console.print(f"user_id: {user_id}")
    console.print(f"mode: {'mem0 extraction' if via_mem0 else 'direct reference embeddings'}")
    if dry_run:
        for file, _rel, idx, record in plan[:10]:
            preview = escape(record.replace("\n", " ")[:180])
            console.print(f"[orange3]{escape(file.name)}[/] chunk {idx}: {preview}")
        if len(plan) > 10:
            console.print(f"... {len(plan) - 10} more records")
        return

    written = 0
    for file, rel, idx, record in plan:
        ok = meshtastic_record(user_id, record) if via_mem0 else meshtastic_upsert_reference(user_id, rel, idx, record)
        if ok:
            written += 1
            console.print(f"[green]{'queued' if via_mem0 else 'indexed'}[/] {file.name} chunk {idx}")
        else:
            console.print(f"[red]failed[/] {file.name} chunk {idx}")
        if delay > 0:
            time.sleep(delay)
    action = "queued for Mem0 extraction" if via_mem0 else "indexed as reference memory"
    console.print(f"[green]{written}/{len(plan)} records {action}.[/]")


@code_app.command("status")
def code_status():
    """Show coding-profile guardrails: loaded model and safe aliases."""
    g = code_guardrails()
    console.print(f"[bold orange1]active profile guess[/] {g['profile']}")
    console.print("[bold orange1]loaded models[/]")
    for m in g["loaded"]:
        console.print(f"  {m}")
    console.print("[bold orange1]safe coding aliases[/] " + (", ".join(g["safe_aliases"]) or "(none)"))
    if g["warning"]:
        console.print(f"[yellow]{g['warning']}[/]")


@code_app.command("doctor")
def code_doctor(
    fix: bool = typer.Option(False, "--fix", help="start missing containers/host services where possible"),
):
    """Check the local coding path from LM Studio through LiteLLM, keys, aliases, and Mem0."""
    if fix:
        for step in recover():
            console.print(f"[cyan]- {step}[/]")
    g = code_guardrails()
    checks = [
        ("LM Studio :1234", _port_open("localhost", 1234)),
        ("LiteLLM readiness", litellm_ready()),
        ("qwen coding model loaded", any("qwen/qwen3.6-35b-a3b" in m for m in g["loaded"])),
        ("Hermes sub-agent model loaded", any("qwen2.5-3b-instruct" in m for m in g["loaded"])),
        ("Cline virtual key present", bool(_env_value("LITELLM_KEY_CLINE"))),
        ("Qwen virtual key present", bool(_env_value("LITELLM_KEY_QWEN"))),
        ("Claude virtual key present", bool(_env_value("LITELLM_KEY_CLAUDE"))),
        ("Hermes virtual key present", bool(_env_value("LITELLM_KEY_HERMES"))),
        ("Mem0 service :8077", mem0_health() is not None),
        ("Qdrant :6333", _port_open("localhost", 6333)),
        ("SearXNG :8081", _port_open("localhost", 8081)),
        ("dashboard ingest :8090", web_health()),
    ]
    models = set(litellm_models("LITELLM_KEY_CLINE"))
    for alias in ("cline", "qwen-code", "claude-local-main", "claude-local-fast"):
        checks.append((f"LiteLLM alias {alias}", alias in models))
    hermes_models = set(litellm_models("LITELLM_KEY_HERMES"))
    checks.append(("LiteLLM alias hermes-subagent", "hermes-subagent" in hermes_models))
    failed = 0
    for name, ok in checks:
        mark = "[green]ok  [/]" if ok else "[red]DOWN[/]"
        console.print(f"  {mark}  {name}")
        failed += 0 if ok else 1
    console.print("")
    code_status()
    if failed:
        console.print(f"\n[yellow]{failed} coding checks failed.[/]")
        raise typer.Exit(1)
    console.print("\n[bold green]coding path looks ready.[/]")


@code_app.command("smoke")
def code_smoke(
    model: str = typer.Option("cline", "--model", "-m", help="LiteLLM model alias to test"),
    key: str = typer.Option("LITELLM_KEY_CLINE", "--key", help=".env key variable to authenticate with"),
    memory_write: bool = typer.Option(False, "--memory-write", help="also test Mem0 write/recall; may load the brain model"),
):
    """Send one tiny coding-model request."""
    ok, msg = litellm_chat_smoke(model, key)
    console.print(("  [green]ok  [/]" if ok else "  [red]FAIL[/]") + f"  LiteLLM chat {model}: {msg[:200]}")
    if not memory_write:
        raise typer.Exit(0 if ok else 1)
    mem_ok = False
    try:
        user_id = "stack:code-smoke"
        # Use a real fact: the brain extracts personal facts/preferences, so a meta-sentence
        # with no facts (correctly) yields nothing to store and the recall comes back empty.
        mem_ok = mem0_record(user_id, "Memory smoke test: my favorite programming language is Rust.")
        recalled = []
        deadline = time.time() + 20
        while time.time() < deadline and not recalled:
            time.sleep(2)
            recalled = mem0_recall(user_id, "favorite programming language", limit=3)
        mem_ok = mem_ok and bool(recalled)
        if recalled:
            mem0_delete_user(user_id)
        console.print(("  [green]ok  [/]" if mem_ok else "  [red]FAIL[/]") + "  Mem0 record/recall")
    except Exception as e:  # noqa: BLE001
        console.print(f"  [red]FAIL[/]  Mem0 record/recall: {e}")
    raise typer.Exit(0 if ok and mem_ok else 1)


@code_app.command("init")
def code_init(
    path: Path = typer.Argument(Path("."), help="project folder to prepare"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Mem0 project name; defaults to folder name"),
    force: bool = typer.Option(False, "--force", help="overwrite existing files"),
):
    """Drop local-stack coding config into a project folder."""
    written = code_project_init(path, project, force)
    if written:
        for f in written:
            console.print(f"[green]wrote[/] {f}")
    else:
        console.print("[yellow]nothing written; files already exist. Use --force to refresh.[/]")


@mineru_app.command("bulk")
def mineru_bulk(
    folder: Path = typer.Argument(..., help="Folder containing PDFs."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output folder."),
    server: str = typer.Option("http://localhost:8000", "--server", help="MinerU API base URL."),
    backend: str = typer.Option("vlm-auto-engine", "--backend", help="MinerU backend."),
    max_pages: int = typer.Option(1000, "--max-pages", help="Maximum pages per PDF."),
    jobs: int = typer.Option(1, "--jobs", help="Concurrent PDFs; keep 1 for VLM/GPU."),
    recursive: bool = typer.Option(False, "--recursive", help="Find PDFs recursively."),
    force: bool = typer.Option(False, "--force", help="Re-run PDFs already marked done."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only list PDFs that would run."),
):
    """Bulk process PDFs through MinerU VLM and download every result."""
    cmd = [sys.executable, str(MINERU_BULK), str(folder),
           "--server", server, "--backend", backend,
           "--max-pages", str(max_pages), "--jobs", str(jobs)]
    if output:
        cmd += ["--output", str(output)]
    if recursive:
        cmd.append("--recursive")
    if force:
        cmd.append("--force")
    if dry_run:
        cmd.append("--dry-run")
    raise typer.Exit(subprocess.run(cmd).returncode)


@openwebui_app.command("import-knowledge")
def openwebui_import_knowledge(
    source: Path = typer.Argument(..., help="Folder containing Markdown files."),
    knowledge: str = typer.Option(..., "--knowledge", "-k", help="Knowledge collection name."),
    group: Optional[str] = typer.Option(None, "--group", "-g", help="Group to create/reuse and grant read access."),
    server: str = typer.Option("http://localhost:3000", "--server", help="OpenWebUI base URL."),
    token: Optional[str] = typer.Option(None, "--token", help="Bearer token; defaults to OPENWEBUI_API_TOKEN."),
    force: bool = typer.Option(False, "--force", help="Upload files even if manifest says completed."),
    background: bool = typer.Option(False, "--background", help="Process files in background."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only list files that would import."),
):
    """Import extracted Markdown into OpenWebUI Knowledge."""
    cmd = [sys.executable, str(OPENWEBUI_IMPORT), str(source),
           "--server", server, "--knowledge", knowledge]
    if group:
        cmd += ["--group", group]
    if token:
        cmd += ["--token", token]
    if force:
        cmd.append("--force")
    if background:
        cmd.append("--background")
    if dry_run:
        cmd.append("--dry-run")
    raise typer.Exit(subprocess.run(cmd).returncode)


@skillsusa_app.command("generate-cards")
def skillsusa_generate_cards(
    source: Path = typer.Option(ROOT / "skillsusa" / "output", "--source", help="Folder containing MinerU Markdown output."),
    output: Path = typer.Option(ROOT / "skillsusa" / "knowledge-cards-generated", "--output", help="Folder for generated retrieval cards."),
    clean: bool = typer.Option(True, "--clean/--no-clean", help="Clear the output folder before generating."),
):
    """Generate normalized retrieval cards from SkillsUSA Markdown exports."""
    cmd = [sys.executable, str(SKILLSUSA_CARDS), "--source", str(source), "--output", str(output)]
    cmd.append("--clean" if clean else "--no-clean")
    raise typer.Exit(subprocess.run(cmd).returncode)


@skillsusa_app.command("smoke")
def skillsusa_smoke(
    knowledge: str = typer.Option("SkillsUSA 2024-26", "--knowledge", "-k", help="Knowledge collection name."),
    cases: Path = typer.Option(SKILLSUSA_SMOKE_CASES, "--cases", help="JSON file of retrieval test cases."),
    server: str = typer.Option("http://localhost:3000", "--server", help="OpenWebUI base URL."),
    token: Optional[str] = typer.Option(None, "--token", help="Bearer token; defaults to OPENWEBUI_API_TOKEN."),
    top_k: int = typer.Option(5, "--top-k", help="Results retrieved per query."),
    raw: bool = typer.Option(False, "--raw", help="Dump retrieved sources per case."),
):
    """Retrieval smoke test: each query must surface the expected competition's card."""
    cmd = [sys.executable, str(OPENWEBUI_SMOKE), str(cases),
           "--server", server, "--knowledge", knowledge, "--top-k", str(top_k)]
    if token:
        cmd += ["--token", token]
    if raw:
        cmd.append("--raw")
    raise typer.Exit(subprocess.run(cmd).returncode)


@app.command()
def up(service: list[str] = typer.Argument(None, help="services, or omit for the core stack")):
    """Start main-stack containers (docker compose up -d)."""
    compose(MAIN_COMPOSE, "up", "-d", *(service or []))


@app.command()
def down(service: list[str] = typer.Argument(None, help="services, or omit for all")):
    """Stop main-stack containers (docker compose stop)."""
    compose(MAIN_COMPOSE, "stop", *(service or []))


@app.command()
def shutdown(
    wsl_down: bool = typer.Option(False, "--wsl", help="also shut down the WSL VM after stopping"),
    unload_models: bool = typer.Option(False, "--unload-models", help="unload LM Studio models too"),
):
    """Gracefully stop ALL AI apps before powering off (host services + every container).

    Order matters for data safety: stop the host services (mem0 writes to Qdrant) first,
    then stop containers with a timeout so SQLite/Qdrant flush to disk. Run this before a
    Windows shutdown/reboot so the WSL VM isn't killed mid-write."""
    console.print("[yellow]1/3 host services...[/]")
    console.print(f"  mem0: stopped {mem0_stop()} process(es)")
    console.print(f"  web:  stopped {web_stop()} process(es)")

    console.print("[yellow]2/3 containers (graceful, -t 30)...[/]")
    for cf in (MINERU_GPU_COMPOSE, MINERU_COMPOSE, MAIN_COMPOSE):
        compose(cf, "stop", "-t", "30")
    # sweep any stragglers (e.g. vllm started under a profile)
    _run(WSL + ["bash", "-lc", "docker ps -q | xargs -r docker stop -t 20"], capture=False)

    if unload_models:
        console.print("[yellow]unloading LM Studio models...[/]")
        lms_unload_all()
    if wsl_down:
        console.print("[yellow]3/3 shutting down the WSL VM...[/]")
        _run(["wsl", "--shutdown"], capture=False)

    console.print("[bold green]all AI apps stopped - safe to power off.[/]")
    if not wsl_down:
        console.print("[dim]containers preserved; reboot or 'stack startup' brings them back. "
                      "LM Studio (a desktop app) keeps running until you close it / power off.[/]")


@app.command()
def startup():
    """Bring the daily stack back up after a shutdown: core containers + host services."""
    console.print("[cyan]core containers...[/]")
    compose(MAIN_COMPOSE, "up", "-d")
    console.print("[cyan]mem0...[/]")
    mem0_start()
    console.print("[cyan]web dashboard...[/]")
    web_start()
    console.print("[cyan]LM Studio scheduler proxy...[/]")
    lm_scheduler_start()
    console.print("[cyan]kimi lazy proxy...[/]")
    kimi_proxy_start()
    console.print("[bold green]up.[/] load models with [white]stack profile chat[/] "
                  "(LM Studio must be running).")


@app.command()
def expose(ports: list[int] = typer.Argument(None, help="ports to expose; omit for defaults"),
           remove: bool = typer.Option(False, "--remove", help="tear the rules down instead")):
    """LAN-expose service ports (portproxy + firewall). Prompts for admin (UAC)."""
    inner = ["-NoExit", "-ExecutionPolicy", "Bypass", "-File", str(EXPOSE_SCRIPT)]
    if ports:
        inner += ["-Ports", ",".join(str(p) for p in ports)]
    if remove:
        inner += ["-Remove"]
    arglist = ",".join(f"'{a}'" for a in inner)
    _run(["powershell", "-NoProfile", "-Command",
          f"Start-Process powershell -Verb RunAs -ArgumentList {arglist}"], capture=False)
    console.print("[yellow]Approve the UAC prompt to apply the firewall/portproxy changes.[/]")


@app.command()
def profile(name: str = typer.Argument(..., help="chat | code | image | extract | extract-gpu | vllm")):
    """Apply a workload profile (does the VRAM juggling for you)."""
    if name not in PROFILES:
        console.print(f"[red]unknown profile '{name}'.[/] options: {', '.join(PROFILES)}")
        raise typer.Exit(1)
    apply_profile(name)
    console.print(f"[bold green]profile '{name}' applied.[/]")


@app.command()
def profiles():
    """List available workload profiles."""
    for k, v in PROFILES.items():
        console.print(f"[bold]{k:9}[/] {v['desc']}")
    console.print("[dim]on-demand host model (not a GPU profile): [white]stack kimi proxy-start[/] "
                  "- Kimi lazy proxy on :8095; backend wakes on :8096[/]")


@app.command()
def monitor(
    start: bool = typer.Option(False, "--start", help="run in the background"),
    stop: bool = typer.Option(False, "--stop", help="stop background monitor processes"),
    status_only: bool = typer.Option(False, "--status", help="show background monitor status"),
    foreground: bool = typer.Option(False, "--foreground", help="run in this terminal until stopped"),
    interval: int = typer.Option(300, "--interval", help="full snapshot interval in seconds"),
    gpu_interval: int = typer.Option(60, "--gpu-interval", help="quick nvidia-smi interval in seconds"),
    duration: int = typer.Option(0, "--duration", help="stop after seconds; 0 means run until stopped"),
    log: Path = typer.Option(MONITOR_LOG, "--log", help="text log path"),
):
    """Low-impact flight recorder for GPU, model, container, and service state."""
    def procs() -> list:
        if not psutil:
            return []
        out = []
        needle = str(MONITOR_SCRIPT).lower()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cl = " ".join(p.info["cmdline"] or []).lower()
                if "flight_recorder.py" in cl or needle in cl:
                    out.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return out

    if stop:
        count = 0
        for p in procs():
            try:
                p.terminate()
                count += 1
            except psutil.Error:
                pass
        console.print(f"stopped {count} monitor process(es).")
        return

    running = procs()
    if status_only:
        if running:
            for p in running:
                console.print(f"[green]running[/] pid {p.pid} -> {log}")
        else:
            console.print(f"[red]not running[/] -> {log}")
        return

    cmd = [str(sys.executable), str(MONITOR_SCRIPT),
           "--interval", str(interval), "--gpu-interval", str(gpu_interval), "--log", str(log)]
    if duration:
        cmd += ["--duration", str(duration)]

    if foreground:
        console.print(f"[cyan]writing monitor log to[/] {log}")
        raise typer.Exit(subprocess.run(cmd).returncode)

    if not start:
        one_shot = [sys.executable, str(MONITOR_SCRIPT), "--interval", str(interval),
                    "--gpu-interval", "0", "--duration", "1", "--log", str(log)]
        console.print(f"[cyan]writing one snapshot to[/] {log}")
        raise typer.Exit(subprocess.run(one_shot).returncode)

    if running:
        console.print(f"[yellow]monitor already running[/] pid(s): {', '.join(str(p.pid) for p in running)}")
        return
    log.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(cmd, cwd=str(ROOT), **_hidden_subprocess_kwargs())
    console.print(f"[green]monitor started[/] -> {log}")
    console.print(f"[dim]full snapshot every {interval}s; GPU quick sample every {gpu_interval}s[/]")


@app.command()
def doctor(fix: bool = typer.Option(False, "--fix", help="recover what's down (restart host services, start missing containers)")):
    """Health-check every service; with --fix, recover the downed ones."""
    checks = health_checks()
    for c in checks:
        mark = "[green]ok  [/]" if c["ok"] else "[red]DOWN[/]"
        console.print(f"  {mark}  {c['name']}")
    down = [c for c in checks if not c["ok"]]
    if not down:
        console.print("\n[bold green]all healthy.[/]")
        return
    if not fix:
        console.print(f"\n[yellow]{len(down)} down. Re-run with [bold]--fix[/] to recover.[/]")
        raise typer.Exit(1)

    for step in recover():
        console.print(f"[cyan]- {step}[/]")
    console.print("\n[bold green]recovery attempted. Re-run [white]stack doctor[/] to confirm.[/]")


@web_app.command("start")
def web_start_cmd(open_browser: bool = typer.Option(True, "--open/--no-open", help="open the dashboard in a browser")):
    """Start the web dashboard (windowless, host process on :8090)."""
    if web_health():
        console.print("[green]already up.[/]")
    elif web_start():
        console.print(f"[bold green]dashboard up at {WEB_URL}[/]")
    else:
        console.print(f"[red]did not come up - check {WEB_LOG}[/]")
        raise typer.Exit(1)
    if open_browser:
        import webbrowser
        webbrowser.open(WEB_URL)


@web_app.command("stop")
def web_stop_cmd():
    """Stop the web dashboard."""
    console.print(f"killed {web_stop()} process(es).")


@web_app.command("restart")
def web_restart_cmd():
    """Restart the web dashboard."""
    console.print(f"killed {web_stop()} process(es).")
    if _port_open("127.0.0.1", 8090, 0.3):
        console.print("[red]web dashboard is still bound to :8090; run from an elevated shell or stop the StackctlWeb task/process manually.[/]")
        raise typer.Exit(1)
    raise typer.Exit(0 if web_start() else 1)


@web_app.command("status")
def web_status_cmd():
    """Is the dashboard up?"""
    console.print(f"{WEB_URL}: " + ("[green]up[/]" if web_health() else "[red]down[/]"))


@lm_scheduler_app.command("start")
def lm_scheduler_start_cmd():
    """Start the LM Studio chat/code lazy scheduler proxy on :1235."""
    ok = lm_scheduler_start()
    console.print(f"{LM_SCHEDULER_URL}: " + ("[green]up[/]" if ok else "[red]failed[/]"))
    raise typer.Exit(0 if ok else 1)


@lm_scheduler_app.command("stop")
def lm_scheduler_stop_cmd():
    """Stop the LM Studio scheduler proxy. Does not unload LM Studio models."""
    console.print(f"killed {lm_scheduler_stop()} process(es).")


@lm_scheduler_app.command("restart")
def lm_scheduler_restart_cmd():
    """Restart the LM Studio scheduler proxy."""
    console.print(f"killed {lm_scheduler_stop()} process(es).")
    ok = lm_scheduler_start()
    console.print(f"{LM_SCHEDULER_URL}: " + ("[green]up[/]" if ok else "[red]failed[/]"))
    raise typer.Exit(0 if ok else 1)


@lm_scheduler_app.command("status")
def lm_scheduler_status_cmd():
    """Show scheduled profile load/idle state."""
    h = lm_scheduler_health()
    console.print(f"{LM_SCHEDULER_URL}: " + ("[green]up[/]" if h else "[red]down[/]"))
    if not h:
        return
    for name, info in h.get("profiles", {}).items():
        idle = info.get("idle_seconds")
        idle_txt = "n/a" if idle is None else f"{int(idle // 60)}m"
        timeout = int(info.get("idle_timeout_seconds", 0) // 60)
        loaded = "[green]loaded[/]" if info.get("loaded") else "[red]cold[/]"
        loading = " [yellow](loading)[/]" if info.get("loading") else ""
        console.print(f"  {name:5} {loaded}{loading} idle {idle_txt} / {timeout}m")


@lm_scheduler_app.command("logs")
def lm_scheduler_logs(lines: int = typer.Option(30, "--lines", "-n", help="tail this many lines")):
    """Tail the LM Studio scheduler proxy log."""
    if not LM_SCHEDULER_LOG.exists():
        console.print("(no log file yet)")
        return
    text = LM_SCHEDULER_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    console.print("\n".join(text[-lines:]) or "(empty)")


@kimi_app.command("start")
def kimi_start_cmd():
    """Launch the Kimi llama-server (CPU+RAM; loading 339GB takes ~5 min)."""
    kimi_proxy_start()
    if kimi_health():
        console.print(f"[green]backend already up at :{KIMI_BACKEND_PORT}.[/]")
        return
    if not KIMI_SERVER.exists():
        console.print(f"[red]llama-server not found:[/] {KIMI_SERVER}  [dim](set KIMI_SERVER in .env)[/]")
        raise typer.Exit(1)
    if not KIMI_MODEL.exists():
        console.print(f"[red]model not found:[/] {KIMI_MODEL}  [dim](set KIMI_MODEL in .env)[/]")
        raise typer.Exit(1)
    console.print("[yellow]launching Kimi llama-server; loading ~339GB into RAM (~5 min)...[/]")
    ok = kimi_start()
    if ok:
        console.print(f"[bold green]kimi backend up at :{KIMI_BACKEND_PORT}[/] "
                      f"(proxy :{KIMI_PROXY_PORT}, LiteLLM alias [white]kimi/kimi-k2.7-code[/]).")
    else:
        console.print(f"[red]did not come up in time - check {KIMI_LOG}[/]")
    raise typer.Exit(0 if ok else 1)


@kimi_app.command("stop")
def kimi_stop_cmd():
    """Stop the Kimi llama-server (frees ~305GB RAM)."""
    console.print(f"killed {kimi_stop()} process(es).")


@kimi_app.command("restart")
def kimi_restart_cmd():
    """Restart the Kimi llama-server (reloads ~339GB, ~5 min)."""
    console.print(f"killed {kimi_stop()} process(es).")
    console.print("[yellow]reloading ~339GB into RAM (~5 min)...[/]")
    raise typer.Exit(0 if kimi_start() else 1)


@kimi_app.command("status")
def kimi_status_cmd():
    """Is Kimi up? Show the serving process and its RAM use."""
    h = kimi_health()
    ph = kimi_proxy_health()
    console.print(f"{KIMI_PROXY_URL} proxy: " + ("[green]up[/]" if ph else "[red]down[/]"))
    if ph:
        console.print(f"  backend: " + ("[green]up[/]" if ph.get("backend") else "[red]down[/]")
                      + (" [yellow](loading)[/]" if ph.get("starting") else ""))
        idle_timeout = ph.get("idle_timeout_seconds", KIMI_IDLE_TIMEOUT_SECONDS)
        idle_seconds = ph.get("idle_seconds")
        idle_text = "n/a" if idle_seconds is None else f"{int(idle_seconds // 60)}m"
        console.print(f"  idle: {idle_text} / {int(idle_timeout // 60)}m")
    console.print(f"{KIMI_URL} backend: " + ("[green]up[/]" if h else "[red]down[/]"))
    for p in kimi_procs():
        try:
            console.print(f"pid {p.pid}: {p.memory_info().rss / 1e9:.0f} GB RSS")
        except Exception:  # noqa: BLE001
            console.print(f"pid {p.pid}")


@kimi_app.command("proxy-start")
def kimi_proxy_start_cmd():
    """Start the lightweight lazy proxy on :8095 without loading Kimi."""
    ok = kimi_proxy_start()
    console.print(f"{KIMI_PROXY_URL}: " + ("[green]up[/]" if ok else "[red]failed[/]"))
    raise typer.Exit(0 if ok else 1)


@kimi_app.command("proxy-stop")
def kimi_proxy_stop_cmd():
    """Stop the lightweight lazy proxy. Does not stop a loaded Kimi backend."""
    console.print(f"killed {kimi_proxy_stop()} proxy process(es).")


@kimi_app.command("logs")
def kimi_logs(lines: int = typer.Option(30, "--lines", "-n", help="tail this many lines")):
    """Tail the llama-server log."""
    if not KIMI_LOG.exists():
        console.print("(no log file yet)")
        return
    text = KIMI_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    console.print("\n".join(text[-lines:]) or "(empty)")


if __name__ == "__main__":
    app()
