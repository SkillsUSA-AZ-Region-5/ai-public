#!/usr/bin/env python3
"""
Retrieval smoke tester for an OpenWebUI Knowledge collection.

For each case it queries OpenWebUI's retrieval endpoint directly (no LLM in the
loop, so results are fast and deterministic) and checks whether the expected
competition's card is retrieved, and at what rank. This validates the disambiguation
the SkillsUSA helper cards exist for: that generic words like "equipment/tools/bring"
route to the right contest instead of a similarly-named one.

Cases live in a JSON file (see skillsusa/smoke-cases.json):
  [{"query": "...", "expect": "Internet of Things (IOT) Smart Home", "note": "..."}]

`expect` is matched (case-insensitive) against each retrieved source's filename.

Token comes from OPENWEBUI_API_TOKEN (env or .env) or --token. Never hardcode it.
Exit code is the number of FAILs (0 = all passed), so it works in CI / `&&` chains.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("Missing dependency: requests. Install it with: python -m pip install requests", file=sys.stderr)
    raise


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def request_json(session: requests.Session, method: str, url: str, **kwargs: Any) -> Any:
    response = session.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def resolve_knowledge_id(session: requests.Session, server: str, name: str, timeout: float) -> str:
    page = 1
    while True:
        payload = request_json(session, "GET", f"{server}/api/v1/knowledge/?page={page}", timeout=timeout)
        items = payload.get("items", []) if isinstance(payload, dict) else (payload or [])
        for item in items:
            if item.get("name", "").lower() == name.lower():
                return item["id"]
        if not items or len(items) < 30:
            raise SystemExit(f"Knowledge collection not found: {name}")
        page += 1


def source_names(meta: dict[str, Any]) -> str:
    """Pull a human filename out of a retrieval metadata blob (shape varies by version)."""
    for key in ("name", "source", "title", "filename"):
        val = meta.get(key)
        if isinstance(val, str) and val:
            return val
    nested = meta.get("meta")
    if isinstance(nested, dict):
        return source_names(nested)
    return ""


def retrieve(session: requests.Session, server: str, knowledge_id: str, query: str,
             k: int, timeout: float) -> list[str]:
    """Return the retrieved source filenames, best match first."""
    payload = request_json(
        session, "POST", f"{server}/api/v1/retrieval/query/collection",
        json={"collection_names": [knowledge_id], "query": query, "k": k},
        timeout=timeout,
    )
    metas = (payload or {}).get("metadatas") or []
    flat = metas[0] if (metas and isinstance(metas[0], list)) else metas
    return [source_names(m) for m in flat if isinstance(m, dict)]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Retrieval smoke tester for an OpenWebUI Knowledge collection.")
    p.add_argument("cases", type=Path, help="JSON file: [{query, expect, note?}]")
    p.add_argument("--server", default=os.environ.get("OPENWEBUI_URL", "http://localhost:3000"))
    p.add_argument("--token", default=os.environ.get("OPENWEBUI_API_TOKEN") or os.environ.get("OPENWEBUI_TOKEN"))
    p.add_argument("--knowledge", "-k", required=True, help="Knowledge collection name.")
    p.add_argument("--top-k", type=int, default=5, help="How many results to retrieve per query.")
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--raw", action="store_true", help="Dump the raw retrieved sources for each case.")
    args = p.parse_args()
    args.server = args.server.rstrip("/")
    if not args.token:
        raise SystemExit("Missing token. Set OPENWEBUI_API_TOKEN or pass --token.")
    return args


def main() -> int:
    load_dotenv(ENV_FILE)
    args = parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {args.token}"})
    knowledge_id = resolve_knowledge_id(session, args.server, args.knowledge, args.timeout)
    print(f"knowledge '{args.knowledge}' -> {knowledge_id}   ({len(cases)} cases, top_k={args.top_k})\n")

    passed = weak = failed = 0
    for case in cases:
        query, expect = case["query"], case["expect"]
        sources = retrieve(session, args.server, knowledge_id, query, args.top_k, args.timeout)
        rank = next((i for i, s in enumerate(sources) if expect.lower() in s.lower()), -1)

        if rank == 0:
            status, passed = "PASS", passed + 1
        elif rank > 0:
            status, weak = f"WEAK@{rank + 1}", weak + 1
        else:
            status, failed = "FAIL", failed + 1

        print(f"[{status:7}] {query}")
        print(f"           expect: {expect}")
        if rank != 0:
            top = sources[0] if sources else "(nothing retrieved)"
            print(f"           top-1 : {top}")
        if args.raw:
            for i, s in enumerate(sources):
                print(f"             {i + 1}. {s}")

    total = passed + weak + failed
    print(f"\n{passed}/{total} PASS (top-1), {weak} WEAK (in top-{args.top_k}), {failed} FAIL")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
