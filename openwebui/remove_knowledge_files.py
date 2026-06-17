#!/usr/bin/env python3
"""Remove matching files from an OpenWebUI Knowledge collection."""
from __future__ import annotations

import argparse
import os
import re
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
    if not response.content:
        return None
    return response.json()


def iter_knowledge(session: requests.Session, server: str, timeout: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = request_json(session, "GET", f"{server}/api/v1/knowledge/?page={page}", timeout=timeout)
        items = payload.get("items", []) if isinstance(payload, dict) else []
        out.extend(items)
        if not items or len(items) < 30:
            break
        page += 1
    return out


def resolve_knowledge_id(session: requests.Session, args: argparse.Namespace) -> str:
    if args.knowledge_id:
        return args.knowledge_id
    if not args.knowledge:
        raise SystemExit("Pass --knowledge or --knowledge-id.")
    for item in iter_knowledge(session, args.server, args.timeout):
        if item.get("name", "").lower() == args.knowledge.lower():
            return item["id"]
    raise SystemExit(f"Knowledge collection not found: {args.knowledge}")


def list_files(session: requests.Session, args: argparse.Namespace, knowledge_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = request_json(
            session,
            "GET",
            f"{args.server}/api/v1/knowledge/{knowledge_id}/files",
            params={"page": page, "limit": 500, "include_content": "false"},
            timeout=args.timeout,
        )
        items = payload.get("items", []) if isinstance(payload, dict) else []
        files.extend(items)
        if len(items) < 500:
            break
        page += 1
    return files


def filename(file_row: dict[str, Any]) -> str:
    return file_row.get("filename") or file_row.get("meta", {}).get("name") or file_row.get("name") or ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove matching files from an OpenWebUI Knowledge collection.")
    parser.add_argument("--server", default=os.environ.get("OPENWEBUI_URL", "http://localhost:3000"))
    parser.add_argument("--token", default=os.environ.get("OPENWEBUI_API_TOKEN") or os.environ.get("OPENWEBUI_TOKEN"))
    parser.add_argument("--knowledge", help="Knowledge collection name.")
    parser.add_argument("--knowledge-id", help="Knowledge collection id.")
    parser.add_argument("--pattern", required=True, help="Regex matched against filenames.")
    parser.add_argument("--delete-file", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    args.server = args.server.rstrip("/")
    if not args.token:
        raise SystemExit("Missing token. Set OPENWEBUI_API_TOKEN or pass --token.")
    return args


def main() -> int:
    load_dotenv(ENV_FILE)
    args = parse_args()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {args.token}"})
    knowledge_id = resolve_knowledge_id(session, args)
    matcher = re.compile(args.pattern)
    targets = [(row.get("id"), filename(row)) for row in list_files(session, args, knowledge_id) if matcher.search(filename(row))]
    print(f"matched {len(targets)} file(s) in knowledge {knowledge_id}")
    for file_id, name in targets:
        print(("would remove" if args.dry_run else "remove") + f": {name} ({file_id})")
        if args.dry_run:
            continue
        response = session.post(
            f"{args.server}/api/v1/knowledge/{knowledge_id}/file/remove",
            params={"delete_file": "true" if args.delete_file else "false"},
            json={"file_id": file_id},
            timeout=args.timeout,
        )
        response.raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
