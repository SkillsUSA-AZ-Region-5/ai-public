#!/usr/bin/env python3
"""
Bulk-submit PDFs to the local MinerU API and download each result.

Defaults are tuned for the GPU/VLM profile:
  - backend=vlm-auto-engine
  - pages 0..999 (1000 pages)
  - table/formula/image analysis enabled
  - async /tasks endpoint with one in-flight job by default
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

try:
    import requests
except ImportError:
    print("Missing dependency: requests. Install it with: python -m pip install requests", file=sys.stderr)
    raise


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
DONE_STATES = {"done", "completed", "complete", "success", "succeeded", "finished"}
FAILED_STATES = {"failed", "fail", "error", "errored", "cancelled", "canceled", "timeout"}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def bool_form(value: bool) -> str:
    return "true" if value else "false"


def safe_name(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._")
    digest = hashlib.sha1(str(path).lower().encode("utf-8")).hexdigest()[:8]
    return f"{stem or 'document'}-{digest}"


def find_pdfs(src: Path, recursive: bool) -> list[Path]:
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(p for p in src.glob(pattern) if p.is_file())


def auth_from_args(args: argparse.Namespace) -> tuple[str, str] | None:
    user = args.user or os.environ.get("MINERU_AUTH_USER")
    password = args.password or os.environ.get("MINERU_AUTH_PASS")
    if not user and not password:
        return None
    if not user or not password:
        raise SystemExit("MinerU auth is incomplete. Set MINERU_AUTH_USER and MINERU_AUTH_PASS, or pass --user/--password.")
    return user, password


def make_session(args: argparse.Namespace) -> requests.Session:
    session = requests.Session()
    auth = auth_from_args(args)
    if auth:
        session.auth = auth
    return session


def first_task_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("task_id", "id", "uuid"):
            value = payload.get(key)
            if value:
                return str(value)
        for key in ("data", "task", "result"):
            found = first_task_id(payload.get(key))
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = first_task_id(item)
            if found:
                return found
    return None


def status_text(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("status", "state", "task_status", "phase"):
            value = payload.get(key)
            if value is not None:
                return str(value).lower()
        for key in ("data", "task", "result"):
            text = status_text(payload.get(key))
            if text:
                return text
    return ""


def walk_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} or value.startswith("/"):
            urls.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            urls.extend(walk_urls(child))
    elif isinstance(value, list):
        for child in value:
            urls.extend(walk_urls(child))
    return urls


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def request_json(session: requests.Session, method: str, url: str, **kwargs) -> Any:
    response = session.request(method, url, **kwargs)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{method} {url} did not return JSON: {response.text[:500]}") from exc


def submit_task(session: requests.Session, args: argparse.Namespace, pdf: Path) -> tuple[str, Any]:
    data = [
        ("backend", args.backend),
        ("parse_method", args.parse_method),
        ("formula_enable", bool_form(args.formula_enable)),
        ("table_enable", bool_form(args.table_enable)),
        ("image_analysis", bool_form(args.image_analysis)),
        ("return_md", bool_form(args.return_md)),
        ("return_middle_json", bool_form(args.return_middle_json)),
        ("return_model_output", bool_form(args.return_model_output)),
        ("return_content_list", bool_form(args.return_content_list)),
        ("return_images", bool_form(args.return_images)),
        ("response_format_zip", bool_form(args.response_format_zip)),
        ("return_original_file", bool_form(args.return_original_file)),
        ("client_side_output_generation", bool_form(args.client_side_output_generation)),
        ("start_page_id", str(args.start_page_id)),
        ("end_page_id", str(args.end_page_id)),
    ]
    for lang in args.lang_list:
        data.append(("lang_list", lang))
    if args.server_url:
        data.append(("server_url", args.server_url))

    with pdf.open("rb") as handle:
        files = [("files", (pdf.name, handle, "application/pdf"))]
        payload = request_json(session, "POST", urljoin(args.server.rstrip("/") + "/", "tasks"),
                               data=data, files=files, timeout=args.submit_timeout)
    task_id = first_task_id(payload)
    if not task_id:
        raise RuntimeError(f"MinerU accepted the request but no task id was found: {payload}")
    return task_id, payload


def poll_task(session: requests.Session, args: argparse.Namespace, task_id: str) -> Any:
    deadline = time.time() + args.timeout
    last_state = ""
    url = urljoin(args.server.rstrip("/") + "/", f"tasks/{task_id}")
    while time.time() < deadline:
        payload = request_json(session, "GET", url, timeout=args.request_timeout)
        state = status_text(payload)
        if state != last_state:
            print(f"  task {task_id}: {state or 'unknown'}")
            last_state = state
        if state in DONE_STATES:
            return payload
        if state in FAILED_STATES:
            raise RuntimeError(f"task {task_id} failed: {payload}")
        time.sleep(args.poll_seconds)
    raise TimeoutError(f"task {task_id} did not finish within {args.timeout} seconds")


def short_segment(value: str, limit: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" ._")
    if not value:
        return "_"
    if len(value) <= limit:
        return value
    suffix = Path(value).suffix
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:8]
    suffix_room = len(suffix) if len(suffix) <= 12 else 0
    prefix_limit = max(8, limit - 9 - suffix_room)
    return f"{value[:prefix_limit].rstrip(' ._')}-{digest}{suffix if suffix_room else ''}"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(1, 1000):
        candidate = path.with_name(f"{stem}-{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not find a unique path for {path}")


def maybe_extract_zip(zip_path: Path, out_dir: Path) -> None:
    if not zip_path.exists() or zip_path.suffix.lower() != ".zip":
        return
    extract_dir = out_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        roots = {Path(info.filename).parts[0] for info in infos if Path(info.filename).parts}
        strip_root = len(roots) == 1
        for info in infos:
            parts = list(Path(info.filename).parts)
            if strip_root and len(parts) > 1:
                parts = parts[1:]
            if not parts:
                continue
            safe_parts = [short_segment(part) for part in parts]
            dest = unique_path(extract_dir.joinpath(*safe_parts))
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, dest.open("wb") as target:
                target.write(source.read())


def download_url(session: requests.Session, args: argparse.Namespace, url: str, out_dir: Path) -> Path:
    absolute = urljoin(args.server.rstrip("/") + "/", url)
    name = Path(urlparse(absolute).path).name or "download.bin"
    dest = out_dir / name
    with session.get(absolute, stream=True, timeout=args.download_timeout) as response:
        response.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    return dest


def download_result(session: requests.Session, args: argparse.Namespace, task_id: str, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    url = urljoin(args.server.rstrip("/") + "/", f"tasks/{task_id}/result")
    response = session.get(url, stream=True, timeout=args.download_timeout)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    saved: list[Path] = []
    if "application/json" in content_type or "text/json" in content_type:
        payload = response.json()
        json_path = out_dir / "result.json"
        write_json(json_path, payload)
        saved.append(json_path)
        for found_url in dict.fromkeys(walk_urls(payload)):
            try:
                saved.append(download_url(session, args, found_url, out_dir))
            except Exception as exc:
                print(f"  warning: could not download nested URL {found_url}: {exc}")
        return saved

    suffix = ".zip" if "zip" in content_type else ".bin"
    dest = out_dir / f"result{suffix}"
    with dest.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    saved.append(dest)
    if args.extract_zip and suffix == ".zip":
        try:
            maybe_extract_zip(dest, out_dir)
        except Exception as exc:
            print(f"  warning: saved result.zip but could not extract it: {exc}")
    return saved


def manifest_append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def process_pdf(args: argparse.Namespace, pdf: Path, index: int, total: int) -> dict[str, Any]:
    started = time.time()
    out_dir = args.output / safe_name(pdf)
    done_marker = out_dir / ".done"
    if done_marker.exists() and not args.force:
        print(f"[{index}/{total}] skip done: {pdf.name}")
        return {"status": "skipped", "source": str(pdf), "output": str(out_dir)}

    print(f"[{index}/{total}] submit: {pdf.name}")
    out_dir.mkdir(parents=True, exist_ok=True)
    row: dict[str, Any] = {"source": str(pdf), "output": str(out_dir), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    session = make_session(args)
    try:
        task_id, submit_payload = submit_task(session, args, pdf)
        row["task_id"] = task_id
        write_json(out_dir / "submit.json", submit_payload)
        poll_payload = poll_task(session, args, task_id)
        write_json(out_dir / "status.json", poll_payload)
        saved = download_result(session, args, task_id, out_dir)
        row.update({"status": "completed", "saved": [str(p) for p in saved]})
        done_marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
        print(f"  done -> {out_dir}")
    except Exception as exc:
        row.update({"status": "failed", "error": str(exc)})
        print(f"  FAILED: {exc}", file=sys.stderr)
    row["duration_seconds"] = round(time.time() - started, 1)
    manifest_append(args.output / "manifest.jsonl", row)
    return row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk process PDFs through MinerU /tasks and download results.")
    parser.add_argument("folder", type=Path, help="Folder containing PDFs.")
    parser.add_argument("-o", "--output", type=Path, help="Output folder. Default: <folder>/mineru-results")
    parser.add_argument("--server", default=os.environ.get("MINERU_API_URL", "http://localhost:8000"))
    parser.add_argument("--user", default=os.environ.get("MINERU_AUTH_USER"))
    parser.add_argument("--password", default=os.environ.get("MINERU_AUTH_PASS"))
    parser.add_argument("--backend", default="vlm-auto-engine")
    parser.add_argument("--parse-method", default="auto")
    parser.add_argument("--lang", action="append", default=None, help="Repeat for multiple lang_list values. Default: en")
    parser.add_argument("--max-pages", type=int, default=1000, help="Maximum pages to process, starting at --start-page-id.")
    parser.add_argument("--start-page-id", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=1, help="Concurrent PDFs. Keep 1 for GPU/VLM unless you know VRAM is safe.")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-run PDFs even if .done exists.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--extract-zip", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--table-enable", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--formula-enable", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--image-analysis", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--return-md", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--return-middle-json", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--return-model-output", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--return-content-list", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--return-images", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--response-format-zip", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--return-original-file", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--client-side-output-generation", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--server-url", default=None, help="Only for *-http-client backends.")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=24 * 60 * 60, help="Per-PDF task timeout in seconds.")
    parser.add_argument("--submit-timeout", type=float, default=300)
    parser.add_argument("--request-timeout", type=float, default=60)
    parser.add_argument("--download-timeout", type=float, default=3600)
    args = parser.parse_args()
    args.folder = args.folder.resolve()
    args.output = (args.output or (args.folder / "mineru-results")).resolve()
    args.lang_list = args.lang or ["en"]
    args.end_page_id = args.start_page_id + args.max_pages - 1
    return args


def main() -> int:
    load_dotenv(ENV_FILE)
    args = parse_args()
    if not args.folder.exists() or not args.folder.is_dir():
        print(f"PDF folder does not exist: {args.folder}", file=sys.stderr)
        return 2

    pdfs = find_pdfs(args.folder, args.recursive)
    print(f"Found {len(pdfs)} PDF(s) in {args.folder}")
    print(f"Backend: {args.backend}; pages: {args.start_page_id}..{args.end_page_id}; output: {args.output}")
    if args.dry_run:
        for pdf in pdfs:
            print(pdf)
        return 0
    if not pdfs:
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    session = make_session(args)

    health_url = urljoin(args.server.rstrip("/") + "/", "health")
    try:
        response = session.get(health_url, timeout=args.request_timeout)
        response.raise_for_status()
    except Exception as exc:
        print(f"MinerU health check failed at {health_url}: {exc}", file=sys.stderr)
        return 1

    failures = 0
    if args.jobs == 1:
        for index, pdf in enumerate(pdfs, 1):
            row = process_pdf(args, pdf, index, len(pdfs))
            failures += row.get("status") == "failed"
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(process_pdf, args, pdf, index, len(pdfs)): pdf
                       for index, pdf in enumerate(pdfs, 1)}
            for future in as_completed(futures):
                row = future.result()
                failures += row.get("status") == "failed"
    print(f"Complete. Failures: {failures}. Manifest: {args.output / 'manifest.jsonl'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
