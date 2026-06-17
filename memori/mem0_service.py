"""
Mem0-backed memory service (replaces the Memori memory_service.py).

Architecture: brain (light LLM, decides what to remember) + nomic embeddings, both via
the LiteLLM gateway; vectors in Qdrant. Recall is fast vector search (no per-query LLM).
Same HTTP contract as before (/health, /recall, /record + Bearer auth) so the OpenWebUI
filter and Cline MCP need no changes.

Runs on the HOST (venv) and reaches LiteLLM at localhost:4000 and Qdrant at localhost:6333.
Run it with the venv's python (see scripts/start-stack.cmd / the scheduled task).
"""
import os
import sys

# pythonw.exe (scheduled task) has no stdout/stderr; redirect so deps don't crash.
if sys.stdout is None or sys.stderr is None:
    _logf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "mem0_service.log"),
                 "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stdout or _logf
    sys.stderr = sys.stderr or _logf

import threading
import concurrent.futures
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

LITELLM_URL = "http://localhost:4000/v1"
# Per-app LiteLLM virtual key (alias: mem0); falls back to the master key if it isn't set.
MASTER = os.environ.get("LITELLM_KEY_MEM0") or os.environ["LITELLM_MASTER_KEY"]
SERVICE_TOKEN = os.environ.get("MEMORI_SERVICE_TOKEN", "")
# Mem0's OpenAI clients also read these from env in some code paths:
os.environ.setdefault("OPENAI_API_KEY", MASTER)
os.environ.setdefault("OPENAI_BASE_URL", LITELLM_URL)

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import uvicorn
from mem0 import Memory

CONFIG = {
    "llm": {"provider": "openai", "config": {
        "model": "brain", "openai_base_url": LITELLM_URL, "api_key": MASTER,
        "temperature": 0.1}},
    # NOTE: do NOT set embedding_dims here - Mem0 would then send a `dimensions` param
    # that LM Studio/LiteLLM rejects for nomic. Collection sizing is set on the vector
    # store below; nomic returns 768-dim vectors natively.
    "embedder": {"provider": "openai", "config": {
        "model": "embed", "openai_base_url": LITELLM_URL, "api_key": MASTER}},
    "vector_store": {"provider": "qdrant", "config": {
        "host": "localhost", "port": 6333,
        "collection_name": "mem0", "embedding_model_dims": 768}},
}

_mem = Memory.from_config(CONFIG)
_lock = threading.Lock()
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)  # background writes

app = FastAPI(title="mem0-service")


def _do_add(uid: str, text: str):
    try:
        with _lock:
            _mem.add(text, user_id=uid)
    except Exception as e:  # noqa: BLE001
        print("mem0 add failed:", e)


def _check_auth(authorization):
    if SERVICE_TOKEN and authorization != f"Bearer {SERVICE_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _flatten(content) -> str:
    if isinstance(content, list):
        return " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content or "")


def _mems(res):
    items = res.get("results", res) if isinstance(res, dict) else res
    out = []
    for r in items or []:
        out.append(r.get("memory") if isinstance(r, dict) else str(r))
    return [m for m in out if m]


class RecallReq(BaseModel):
    user_id: str
    query: str
    limit: int = 5


class RecordReq(BaseModel):
    user_id: str
    text: str


@app.get("/health")
def health():
    return {"ok": True, "engine": "mem0"}


@app.post("/recall")
def recall(r: RecallReq, authorization: str = Header(default=None)):
    _check_auth(authorization)
    with _lock:
        res = _mem.search(query=_flatten(r.query), filters={"user_id": (r.user_id or "anon")}, limit=r.limit)
    return {"memories": _mems(res)}


@app.post("/record")
def record(r: RecordReq, authorization: str = Header(default=None)):
    _check_auth(authorization)
    text = _flatten(r.text).strip()
    if text:
        _pool.submit(_do_add, (r.user_id or "anon"), text)  # fire-and-forget (brain ~6s)
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8077)
