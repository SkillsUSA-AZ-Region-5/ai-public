#!/usr/bin/env python3
"""
Meshtastic-only Mem0 service.

This runs beside the default Mem0 service but writes to a separate Qdrant
collection, so Hermes work for Meshtastic does not share memory with the main
chat/coding stack.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

os.environ.setdefault("MEM0_SERVICE_NAME", "meshtastic-mem0")
os.environ.setdefault("MEM0_SERVICE_PORT", os.environ.get("MESHTASTIC_MEM0_PORT", "8078"))
os.environ.setdefault("MEM0_COLLECTION_NAME", os.environ.get("MESHTASTIC_MEM0_COLLECTION", "mem0_meshtastic"))
os.environ.setdefault("MEM0_LOG_FILE", "meshtastic_mem0_service.log")

if os.environ.get("MESHTASTIC_MEM0_SERVICE_TOKEN"):
    os.environ.setdefault("MEM0_SERVICE_TOKEN", os.environ["MESHTASTIC_MEM0_SERVICE_TOKEN"])

from mem0_service import app, SERVICE_HOST, SERVICE_PORT  # noqa: E402
import uvicorn  # noqa: E402


if __name__ == "__main__":
    uvicorn.run(app, host=SERVICE_HOST, port=SERVICE_PORT)
