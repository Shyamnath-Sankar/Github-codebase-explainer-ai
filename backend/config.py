"""
Configuration module for GitHub Codebase Explainer.
Loads environment variables from .env file and exposes them as constants.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file in the project root
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# ─── Together AI (OpenAI-compatible) ───────────────────────────
TOGETHER_API_KEY = os.getenv("OPENAI_API_KEY", os.getenv("TOGETHER_API_KEY", ""))
TOGETHER_BASE_URL = os.getenv("OPENAI_BASE_URL", os.getenv("TOGETHER_BASE_URL", "https://api.together.xyz/v1"))

# ─── Endee Vector Database ─────────────────────────────────────
ENDEE_URL = os.getenv("ENDEE_URL", "http://localhost:8080/api/v1")
ENDEE_AUTH_TOKEN = os.getenv("ENDEE_AUTH_TOKEN", "")
INDEX_NAME = os.getenv("INDEX_NAME", "codebase_index")

# ─── Model Configuration ──────────────────────────────────────
EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-large-instruct")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")

# ─── Ingestion Limits ─────────────────────────────────────────
MAX_CHUNK_CHARS = 800        # Max chars per code chunk (~200-300 tokens)
MAX_META_CHARS = 500         # Max characters stored in vector metadata
MAX_EMBED_CHARS = 1500       # Max chars sent to embedding API (512 token limit)
UPSERT_BATCH_SIZE = 50       # Max vectors per Endee upsert call
