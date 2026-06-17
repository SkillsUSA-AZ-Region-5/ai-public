#!/usr/bin/env python3
"""
Import a folder of Markdown files into an OpenWebUI Knowledge collection.

The importer can create/reuse an OpenWebUI group, create/reuse a Knowledge
collection, upload each .md file, and grant the group read access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
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


def endpoint(args: argparse.Namespace, path: str) -> str:
    return args.server.rstrip("/") + path


def request_json(session: requests.Session, method: str, url: str, **kwargs) -> Any:
    response = session.request(method, url, **kwargs)
    response.raise_for_status()
    if not response.content:
        return None
    return response.json()


def find_markdown(src: Path, dedupe_content: bool) -> list[Path]:
    files = sorted(p for p in src.rglob("*.md") if p.is_file())
    if not dedupe_content:
        return files
    seen: set[str] = set()
    out: list[Path] = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        out.append(path)
    return out


def load_completed(manifest: Path) -> set[str]:
    completed: set[str] = set()
    if not manifest.exists():
        return completed
    for raw in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "completed" and row.get("source"):
            completed.add(str(Path(row["source"]).resolve()))
    return completed


def append_manifest(manifest: Path, row: dict[str, Any]) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def get_groups(session: requests.Session, args: argparse.Namespace) -> list[dict]:
    return request_json(session, "GET", endpoint(args, "/api/v1/groups/"), timeout=args.timeout) or []


def get_or_create_group(session: requests.Session, args: argparse.Namespace) -> dict | None:
    if not args.group:
        return None
    for group in get_groups(session, args):
        if group.get("name", "").lower() == args.group.lower():
            print(f"group exists: {group['name']} ({group['id']})")
            return group
    payload = {
        "name": args.group,
        "description": args.group_description or f"Imported knowledge access group for {args.knowledge}",
        "permissions": {},
        "data": {},
    }
    group = request_json(session, "POST", endpoint(args, "/api/v1/groups/create"), json=payload, timeout=args.timeout)
    print(f"group created: {group['name']} ({group['id']})")
    return group


def iter_knowledge_pages(session: requests.Session, args: argparse.Namespace) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        payload = request_json(session, "GET", endpoint(args, f"/api/v1/knowledge/?page={page}"), timeout=args.timeout)
        items = payload.get("items", []) if isinstance(payload, dict) else []
        out.extend(items)
        if not items or len(items) < 30:
            break
        page += 1
    return out


def grants_for_group(group: dict | None, write: bool) -> list[dict]:
    if not group:
        return []
    grants = [{"principal_type": "group", "principal_id": group["id"], "permission": "read"}]
    if write:
        grants.append({"principal_type": "group", "principal_id": group["id"], "permission": "write"})
    return grants


def get_or_create_knowledge(session: requests.Session, args: argparse.Namespace, group: dict | None) -> dict:
    for knowledge in iter_knowledge_pages(session, args):
        if knowledge.get("name", "").lower() == args.knowledge.lower():
            print(f"knowledge exists: {knowledge['name']} ({knowledge['id']})")
            return update_access(session, args, knowledge, group)

    payload = {
        "name": args.knowledge,
        "description": args.description or f"Imported from {args.source}",
        "access_grants": grants_for_group(group, args.group_write),
    }
    knowledge = request_json(session, "POST", endpoint(args, "/api/v1/knowledge/create"), json=payload, timeout=args.timeout)
    print(f"knowledge created: {knowledge['name']} ({knowledge['id']})")
    return knowledge


def update_access(session: requests.Session, args: argparse.Namespace, knowledge: dict, group: dict | None) -> dict:
    grants = grants_for_group(group, args.group_write)
    if not grants:
        return knowledge
    payload = {"access_grants": grants}
    updated = request_json(
        session,
        "POST",
        endpoint(args, f"/api/v1/knowledge/{knowledge['id']}/access/update"),
        json=payload,
        timeout=args.timeout,
    )
    return updated or knowledge


def upload_markdown(session: requests.Session, args: argparse.Namespace, knowledge: dict, path: Path) -> dict:
    metadata = {
        "knowledge_id": knowledge["id"],
        "source_path": str(path),
        "imported_by": "local-ai-stack/openwebui/import_knowledge.py",
    }
    params = {
        "process": "true",
        "process_in_background": "true" if args.background else "false",
    }
    with path.open("rb") as handle:
        files = {"file": (path.name, handle, "text/markdown")}
        data = {"metadata": json.dumps(metadata)}
        return request_json(
            session,
            "POST",
            endpoint(args, "/api/v1/files/"),
            params=params,
            files=files,
            data=data,
            timeout=args.upload_timeout,
        )


def attach_file_to_knowledge(session: requests.Session, args: argparse.Namespace, knowledge: dict, file_id: str) -> dict:
    response = session.post(
        endpoint(args, f"/api/v1/knowledge/{knowledge['id']}/file/add"),
        json={"file_id": file_id},
        timeout=args.upload_timeout,
    )
    if response.status_code == 400 and "Duplicate content detected" in response.text:
        return {"status": "duplicate", "detail": response.text}
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


def poll_file(session: requests.Session, args: argparse.Namespace, file_id: str) -> dict:
    if args.background:
        return {"status": "submitted"}
    # Synchronous upload should already be processed, but check once so failures are visible.
    try:
        payload = request_json(session, "GET", endpoint(args, f"/api/v1/files/{file_id}"), timeout=args.timeout)
        return (payload or {}).get("data", {}) or {}
    except Exception as exc:
        return {"status": "unknown", "warning": str(exc)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Markdown files into OpenWebUI Knowledge.")
    parser.add_argument("source", type=Path, help="Folder containing extracted Markdown files.")
    parser.add_argument("--server", default=os.environ.get("OPENWEBUI_URL", "http://localhost:3000"))
    parser.add_argument("--token", default=os.environ.get("OPENWEBUI_API_TOKEN") or os.environ.get("OPENWEBUI_TOKEN"))
    parser.add_argument("--knowledge", required=True, help="Knowledge collection name.")
    parser.add_argument("--description", default=None)
    parser.add_argument("--group", default=None, help="Create/reuse this OpenWebUI group and grant access.")
    parser.add_argument("--group-description", default=None)
    parser.add_argument("--group-write", action="store_true", help="Grant group write access too.")
    parser.add_argument("--force", action="store_true", help="Upload files even if manifest says completed.")
    parser.add_argument("--dedupe-content", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--background", action="store_true", help="Submit file processing in background.")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--upload-timeout", type=float, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.source = args.source.resolve()
    if not args.token:
        raise SystemExit("Missing token. Set OPENWEBUI_API_TOKEN or pass --token.")
    return args


def main() -> int:
    load_dotenv(ENV_FILE)
    args = parse_args()
    if not args.source.exists():
        print(f"source folder does not exist: {args.source}", file=sys.stderr)
        return 2
    files = find_markdown(args.source, args.dedupe_content)
    manifest = args.source / "openwebui-import-manifest.jsonl"
    completed = set() if args.force else load_completed(manifest)
    pending = [p for p in files if str(p.resolve()) not in completed]

    print(f"Found {len(files)} Markdown file(s); pending {len(pending)}")
    if args.dry_run:
        for path in pending:
            print(path)
        return 0

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {args.token}"})
    group = get_or_create_group(session, args)
    knowledge = get_or_create_knowledge(session, args, group)

    failures = 0
    for index, path in enumerate(pending, 1):
        started = time.time()
        row = {
            "source": str(path),
            "knowledge_id": knowledge["id"],
            "knowledge": knowledge.get("name"),
            "group_id": group.get("id") if group else None,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            print(f"[{index}/{len(pending)}] upload: {path.name}")
            uploaded = upload_markdown(session, args, knowledge, path)
            file_id = uploaded["id"]
            attach_result = attach_file_to_knowledge(session, args, knowledge, file_id)
            file_status = poll_file(session, args, file_id)
            row.update({
                "status": "completed",
                "file_id": file_id,
                "attached_to_knowledge": True,
                "attach_status": attach_result.get("status", "attached") if isinstance(attach_result, dict) else "attached",
                "filename": uploaded.get("filename") or path.name,
                "file_status": file_status,
            })
        except Exception as exc:
            failures += 1
            row.update({"status": "failed", "error": str(exc)})
            print(f"  FAILED: {exc}", file=sys.stderr)
        row["duration_seconds"] = round(time.time() - started, 1)
        append_manifest(manifest, row)

    print(f"Done. Imported: {len(pending) - failures}; failures: {failures}; manifest: {manifest}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
