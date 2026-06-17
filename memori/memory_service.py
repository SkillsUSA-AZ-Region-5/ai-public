r"""
Local memory HTTP service for OpenWebUI (and any other client).

Per-account memory: callers pass `user_id` (OpenWebUI sends the logged-in user's id).
Runs on the HOST in the memori venv (which already has memorisdk + the GPU-backed
extraction model via LM Studio). Binds 0.0.0.0 so the OpenWebUI container can reach
it at the host LAN IP (`HOST_LAN_IP:8077` from `.env`).

Run:
    .\.venv\Scripts\python.exe memory_service.py

Endpoints:
    GET  /health
    POST /recall  {"user_id": "...", "query": "...", "limit": 5}  -> {"memories": [..]}
    POST /record  {"user_id": "...", "text": "..."}               -> {"ok": true}
"""
import os
import sys

# When launched via pythonw.exe (windowless, used by the MemoriMemoryService scheduled
# task), sys.stdout/sys.stderr are None and libraries like uvicorn/transformers crash
# writing to them. Redirect to a log file so the service runs headless.
if sys.stdout is None or sys.stderr is None:
    _logf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "memory_service.log"),
                 "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stdout or _logf
    sys.stderr = sys.stderr or _logf

import threading
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import uvicorn
import memori_config

app = FastAPI(title="memori-service")

# Shared-secret auth: clients must send `Authorization: Bearer <MEMORI_SERVICE_TOKEN>`.
# /health stays open for monitoring. If the token is unset, auth is disabled (dev only).
SERVICE_TOKEN = os.environ.get("MEMORI_SERVICE_TOKEN", "")


def _check_auth(authorization):
    if SERVICE_TOKEN and authorization != f"Bearer {SERVICE_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")

# One enabled Memori instance per user_id (lazy). Cached so we don't re-init per call.
_instances: dict[str, object] = {}
_lock = threading.Lock()


def _get(user_id: str):
    uid = (user_id or "anon").strip() or "anon"
    with _lock:
        m = _instances.get(uid)
        if m is None:
            m = memori_config.make_memori(user_id=uid)
            _instances[uid] = m
        return m


def _flatten(content) -> str:
    # OpenWebUI message content can be a string or a list of parts.
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    return str(content or "")


class RecallReq(BaseModel):
    user_id: str
    query: str
    limit: int = 5


class RecordReq(BaseModel):
    user_id: str
    text: str


@app.get("/health")
def health():
    return {"ok": True, "users_cached": len(_instances)}


@app.post("/recall")
def recall(r: RecallReq, authorization: str = Header(default=None)):
    _check_auth(authorization)
    res = _get(r.user_id).retrieve_context(_flatten(r.query), limit=r.limit)
    out = []
    for x in res or []:
        pd = x.get("processed_data", {}) if isinstance(x, dict) else {}
        out.append(pd.get("content") or pd.get("summary") or str(x)[:300])
    return {"memories": out}


@app.post("/record")
def record(r: RecordReq, authorization: str = Header(default=None)):
    _check_auth(authorization)
    text = _flatten(r.text).strip()
    if text:
        _get(r.user_id).add(text)
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8077)
