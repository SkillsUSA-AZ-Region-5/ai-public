#!/usr/bin/env python3
"""
Low-impact flight recorder for the local AI stack.

The recorder appends plain text snapshots to manage/stack-monitor.log. It only
reads local state: no model prompts, no recovery actions, no service restarts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


ROOT = Path(os.environ.get("STACK_ROOT") or Path(__file__).resolve().parent.parent)
LOG_PATH = ROOT / "manage" / "stack-monitor.log"
WSL_DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu-24.04")


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        if err:
            out = f"{out}\nSTDERR: {err}".strip()
        return p.returncode, out
    except Exception as e:  # noqa: BLE001
        return 999, f"{type(e).__name__}: {e}"


def get_text(url: str, timeout: float = 2.0, token: str = "") -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(4096).decode("utf-8", errors="replace")
            return 200 <= r.status < 300, body.strip()
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def section(title: str, body: str) -> str:
    return f"\n[{title}]\n{body.strip() if body.strip() else '(empty)'}\n"


def system_block() -> str:
    lines = [f"host={socket.gethostname()}"]
    if psutil:
        cpu = psutil.cpu_percent(interval=0.2)
        vm = psutil.virtual_memory()
        lines.append(f"cpu_pct={cpu:.1f}")
        lines.append(
            f"ram_used_gb={vm.used / 1e9:.1f} ram_total_gb={vm.total / 1e9:.1f} "
            f"ram_pct={vm.percent:.1f}"
        )
        lines.append(f"boot_time={dt.datetime.fromtimestamp(psutil.boot_time()).isoformat(timespec='seconds')}")
    rc, uptime = run(["powershell", "-NoProfile", "-Command",
                      "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime"], timeout=10)
    if rc == 0 and uptime:
        lines.append(f"windows_boot={uptime}")
    return "\n".join(lines)


def nvidia_block(detail: bool) -> str:
    query = (
        "timestamp,index,name,pstate,temperature.gpu,utilization.gpu,utilization.memory,"
        "memory.used,memory.total,power.draw,clocks.sm,clocks.mem,pcie.link.gen.current,"
        "pcie.link.width.current"
    )
    rc, out = run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"], timeout=20)
    text = f"gpu_query_rc={rc}\n{out}"
    if detail:
        rc2, out2 = run(["nvidia-smi"], timeout=20)
        text += f"\n\nnvidia_smi_rc={rc2}\n{out2}"
    return text


def lmstudio_block() -> str:
    rc, out = run(["lms", "ps", "--json"], timeout=20)
    if rc != 0:
        return f"lms_ps_rc={rc}\n{out}"
    try:
        models = json.loads(out)
        rows = []
        for m in models:
            rows.append(
                "id={id} state={state} size={size} ctx={ctx} parallel={parallel} device={device} ttl={ttl}".format(
                    id=m.get("identifier") or m.get("modelKey") or m.get("path") or "?",
                    state=m.get("state") or m.get("status") or "?",
                    size=m.get("sizeBytes") or m.get("size") or "?",
                    ctx=m.get("contextLength") or m.get("context_length") or "?",
                    parallel=m.get("parallelism") or m.get("parallel") or "?",
                    device=m.get("device") or "?",
                    ttl=m.get("ttlMs") or m.get("ttl") or "",
                )
            )
        return "lms_ps_rc=0\n" + ("\n".join(rows) if rows else "no loaded models")
    except Exception as e:  # noqa: BLE001
        return f"lms_ps_rc=0 parse_error={type(e).__name__}: {e}\n{out[:4000]}"


def docker_block() -> str:
    rc_path, repo_wsl = run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "wslpath", str(ROOT)], timeout=10)
    repo = repo_wsl.splitlines()[0] if rc_path == 0 and repo_wsl.strip() else "/srv/local-ai-stack"
    bash = (
        f"cd {shlex.quote(repo)} && "
        "docker compose ps --format 'table {{.Name}}\\t{{.Service}}\\t{{.State}}\\t{{.Status}}' && "
        "echo __STATS__ && "
        "docker stats --no-stream --format "
        "'{{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}\\t{{.MemPerc}}\\t{{.PIDs}}'"
    )
    rc, out = run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-lc", bash], timeout=45)
    return f"docker_rc={rc}\n{out}"


def health_block() -> str:
    lmstudio_key = os.environ.get("OPENAI_API_KEY", "")
    checks = [
        ("lmstudio", "http://127.0.0.1:1234/v1/models", lmstudio_key),
        ("lm-scheduler", "http://127.0.0.1:1235/health", ""),
        ("litellm", "http://127.0.0.1:4000/health/liveliness", ""),
        ("openwebui", "http://127.0.0.1:3000/health", ""),
        ("mem0", "http://127.0.0.1:8077/health", ""),
        ("stack-web", "http://127.0.0.1:8090/api/health", ""),
        ("kimi-proxy", "http://127.0.0.1:8095/health", ""),
        ("kimi-backend", "http://127.0.0.1:8096/health", ""),
        ("hermes-dashboard", "http://127.0.0.1:9119", ""),
    ]
    lines = []
    for name, url, token in checks:
        ok, body = get_text(url, token=token)
        short = body.replace("\n", " ")[:240]
        lines.append(f"{name}: {'ok' if ok else 'down'} {short}")
    return "\n".join(lines)


def redact_cmdline(text: str) -> str:
    text = re.sub(r"(--api-key\s+)\S+", r"\1<redacted>", text, flags=re.IGNORECASE)
    text = re.sub(r"(--openai-api-key\s+)\S+", r"\1<redacted>", text, flags=re.IGNORECASE)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._:-]+", r"\1<redacted>", text, flags=re.IGNORECASE)
    return text


def process_block() -> str:
    if not psutil:
        return "psutil unavailable"
    needles = ("llama-server", "kimi_lazy_proxy.py", "lmstudio_scheduler_proxy.py", "mem0_service.py", "webapp.py")
    rows = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            cmdline = " ".join(p.info.get("cmdline") or [])
            name = p.info.get("name") or ""
            haystack = f"{name} {cmdline}".lower()
            if any(n in haystack for n in needles):
                mem = p.info.get("memory_info")
                rss_gb = (mem.rss / 1e9) if mem else 0
                vms_gb = (mem.vms / 1e9) if mem else 0
                rows.append(
                    f"pid={p.pid} name={name} rss_gb={rss_gb:.1f} vms_gb={vms_gb:.1f} "
                    f"cmd={redact_cmdline(cmdline)[:500]}"
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return "\n".join(rows) if rows else "no watched host processes found"


def recent_errors_block() -> str:
    targets = [
        ROOT / "manage" / "kimi-server.log",
        ROOT / "manage" / "kimi-lazy-proxy.log",
        ROOT / "manage" / "lmstudio-scheduler-proxy.log",
        ROOT / "manage" / "webapp.log",
        ROOT / "memori" / "data" / "mem0_service.log",
    ]
    keywords = ("error", "fail", "traceback", "exception", "cuda", "oom", "out of memory", "unload", "warming")
    lines = []
    for path in targets:
        if not path.exists():
            lines.append(f"{path.name}: missing")
            continue
        try:
            tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
            hits = [x for x in tail if any(k in x.lower() for k in keywords)][-12:]
            lines.append(f"{path.name}:")
            lines.extend(f"  {x[:800]}" for x in hits)
            if not hits:
                lines.append("  no recent keyword hits")
        except Exception as e:  # noqa: BLE001
            lines.append(f"{path.name}: read failed: {e}")
    return "\n".join(lines)


def write_snapshot(path: Path, detail_gpu: bool, include_errors: bool) -> None:
    ts = dt.datetime.now().isoformat(timespec="seconds")
    parts = [f"\n===== {ts} =====\n"]
    parts.append(section("system", system_block()))
    parts.append(section("nvidia", nvidia_block(detail_gpu)))
    parts.append(section("lmstudio", lmstudio_block()))
    parts.append(section("health", health_block()))
    parts.append(section("host-processes", process_block()))
    parts.append(section("docker", docker_block()))
    if include_errors:
        parts.append(section("recent-log-keywords", recent_errors_block()))
    append(path, "".join(parts))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append low-impact local AI stack snapshots to a text log.")
    p.add_argument("--interval", type=int, default=int(os.environ.get("STACK_MONITOR_INTERVAL", "300")),
                   help="full snapshot interval in seconds, default 300")
    p.add_argument("--gpu-interval", type=int, default=int(os.environ.get("STACK_MONITOR_GPU_INTERVAL", "60")),
                   help="light nvidia-smi interval in seconds between full snapshots, default 60")
    p.add_argument("--duration", type=int, default=0, help="stop after this many seconds; 0 runs until stopped")
    p.add_argument("--log", default=str(LOG_PATH), help="log file path")
    p.add_argument("--no-errors", action="store_true", help="skip recent keyword scan of local logs")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.log)
    interval = max(30, args.interval)
    gpu_interval = max(0, args.gpu_interval)
    end_at = time.time() + args.duration if args.duration > 0 else None
    append(path, f"\n===== monitor started {dt.datetime.now().isoformat(timespec='seconds')} "
                 f"interval={interval}s gpu_interval={gpu_interval}s pid={os.getpid()} =====\n")
    next_full = 0.0
    next_gpu = 0.0
    while True:
        now = time.time()
        if end_at and now >= end_at:
            append(path, f"===== monitor stopped {dt.datetime.now().isoformat(timespec='seconds')} duration reached =====\n")
            return 0
        if now >= next_full:
            write_snapshot(path, detail_gpu=True, include_errors=not args.no_errors)
            next_full = now + interval
            next_gpu = now + gpu_interval if gpu_interval else next_full
        elif gpu_interval and now >= next_gpu:
            ts = dt.datetime.now().isoformat(timespec="seconds")
            append(path, f"\n===== {ts} gpu quick sample =====\n{nvidia_block(detail=False)}\n")
            next_gpu = now + gpu_interval
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
