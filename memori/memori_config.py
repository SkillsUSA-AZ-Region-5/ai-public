"""
Shared Memori configuration for the local AI box.  (VERIFIED against memorisdk 2.3.3)

- Memory-extraction LLM runs LOCALLY on LM Studio (nothing leaves the machine).
- Storage is a single SQLite file shared by every project => cross-project memory.

Verified behavior on this box:
- Extraction model = google/gemma-4-26b-a4b-qat, loaded with FULL GPU offload in LM
  Studio (`lms load google/gemma-4-26b-a4b-qat --gpu max`). On CPU it is ~30x slower.
- Ingestion (add / record_conversation) is ASYNC: a background thread calls the LLM
  and populates long-term memory after ~10-15s. enable() + auto_ingest=True are required.
- Recall (retrieve_context) takes ~9s and returns list[dict] with processed_data.content.
- Use user_id (not the deprecated `namespace`) to partition memory.
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from memori import Memori
from memori.core.providers import ProviderConfig

# Load the shared .env at the project root (one level up from this file).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# LM Studio's OpenAI-compatible server (host). The MCP server + Cline run on Windows,
# so localhost is correct here (containers would use the LAN IP instead).
LMSTUDIO_BASE_URL = "http://localhost:1234/v1"

# Small/fast MoE extraction model (4B active), kept loaded on GPU in LM Studio.
EXTRACTION_MODEL = "google/gemma-4-26b-a4b-qat"
EXTRACTION_TIMEOUT = 300.0   # extraction/search prompts can be large; be generous

# One DB file for all projects => shared, cross-project memory.
DB_PATH = Path(__file__).parent / "data" / "memory.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

provider = ProviderConfig.from_custom(
    base_url=LMSTUDIO_BASE_URL,
    api_key=os.environ["OPENAI_API_KEY"],   # LM Studio enforces this (from .env)
    model=EXTRACTION_MODEL,
    timeout=EXTRACTION_TIMEOUT,
    max_retries=1,
)


def make_memori(user_id: str = "global") -> Memori:
    """Return an ENABLED Memori instance bound to the shared local SQLite store.

    user_id partitions memory. We use a per-project user_id PLUS a shared 'global'
    one (see mcp_server.py) so the agent recalls project-specific and cross-project
    facts together. enable() + auto_ingest start the background extraction pipeline.
    """
    m = Memori(
        database_connect=DATABASE_URL,
        provider_config=provider,
        user_id=user_id,
        auto_ingest=True,
        conscious_ingest=False,
    )
    m.enable()
    return m
