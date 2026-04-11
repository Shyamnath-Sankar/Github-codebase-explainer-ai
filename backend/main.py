"""
FastAPI application for GitHub Codebase Explainer.
Exposes three endpoints:
  - POST /ingest  → Clone & index a GitHub repo
  - POST /ask     → Ask questions about the indexed codebase
  - GET  /status  → Check how many vectors are in the index
"""

import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional

from config import INDEX_NAME, ENDEE_URL, ENDEE_AUTH_TOKEN
from ingest import ingest_repository
from agent import ask_question

# ─── Logging Setup ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-12s │ %(levelname)-5s │ %(message)s",
)
logger = logging.getLogger("main")

# ─── FastAPI App ───────────────────────────────────────────────
app = FastAPI(
    title="GitHub Codebase Explainer",
    description=(
        "AI-powered tool that ingests any GitHub repository and answers "
        "questions about its code using semantic search (Endee) + LLM (Together AI)."
    ),
    version="1.0.0",
)

# ─── CORS Middleware (allow all origins for dev) ───────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Serve Frontend Static Files Path ─────────────────────────
import os
frontend_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend')


# ─── Request / Response Models ─────────────────────────────────
class IngestRequest(BaseModel):
    repo_url: str = Field(
        ...,
        description="Full GitHub repository URL (e.g. https://github.com/user/repo)",
        examples=["https://github.com/pallets/flask"],
    )

class AskRequest(BaseModel):
    question: str = Field(
        ...,
        description="The question to ask about the codebase",
        examples=["What is the main architecture of this project?"],
    )
    mode: Optional[str] = Field(
        default="explain",
        description="Query mode: 'explain' | 'eli5' | 'bugs' | 'search'",
    )




@app.post("/ingest")
async def ingest_endpoint(request: IngestRequest):
    """
    Ingest a GitHub repository:
    1. Clone the repo
    2. Parse all Python files via AST (extract functions & classes)
    3. Embed each chunk using Together AI embeddings
    4. Upsert vectors to Endee index
    """
    repo_url = request.repo_url.strip()

    # Basic URL validation
    if not repo_url.startswith("https://github.com/"):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL. Please provide a valid GitHub repository URL (https://github.com/user/repo)."
        )

    logger.info(f"📥 Ingesting repository: {repo_url}")
    result = ingest_repository(repo_url)

    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])

    return result


@app.post("/ask")
async def ask_endpoint(request: AskRequest):
    """
    Ask a question about the ingested codebase:
    1. Embed the question
    2. Query Endee for top-5 semantically similar code chunks
    3. Build prompt with code context
    4. Call Together AI LLM with mode-specific system prompt
    5. Return answer with source citations
    """
    question = request.question.strip()
    mode = request.mode or "explain"

    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    valid_modes = ["explain", "eli5", "bugs", "search"]
    if mode not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{mode}'. Choose from: {valid_modes}"
        )

    logger.info(f"🤔 Question [{mode}]: {question[:80]}...")
    result = ask_question(question, mode)
    return result


@app.get("/status")
async def status_endpoint():
    """
    Check the status of the Endee vector index.
    Returns the number of indexed vectors and index configuration.
    """
    try:
        from endee import Endee
        client = Endee(ENDEE_AUTH_TOKEN) if ENDEE_AUTH_TOKEN else Endee()
        client.set_base_url(ENDEE_URL)

        # Try to get index info
        try:
            index = client.get_index(name=INDEX_NAME)
            # Try to get vector count via describe or a lightweight query
            info = {
                "status": "connected",
                "index_name": INDEX_NAME,
                "endee_url": ENDEE_URL,
                "message": f"Index '{INDEX_NAME}' exists and is ready.",
            }
        except Exception:
            info = {
                "status": "no_index",
                "index_name": INDEX_NAME,
                "endee_url": ENDEE_URL,
                "message": f"Index '{INDEX_NAME}' does not exist yet. Ingest a repo first.",
            }

        return info

    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return {
            "status": "error",
            "message": f"Could not connect to Endee: {str(e)}",
            "endee_url": ENDEE_URL,
        }


# ─── Health Check ──────────────────────────────────────────────
@app.get("/health")
async def health_check():
    """Simple health check endpoint."""
    return {"status": "healthy", "service": "GitHub Codebase Explainer"}

# ─── Serve Frontend Static Files (Catch-all) ───────────────────
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
