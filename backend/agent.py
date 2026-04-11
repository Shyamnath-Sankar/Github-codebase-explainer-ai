"""
RAG Agent module for GitHub Codebase Explainer.
Queries Endee for semantically similar code chunks → builds context →
calls Together AI LLM (OpenAI-compatible) → returns answer with sources.
"""

import logging
from typing import Dict, Any, List

from openai import OpenAI
from endee import Endee

from config import (
    TOGETHER_API_KEY, TOGETHER_BASE_URL, ENDEE_URL, ENDEE_AUTH_TOKEN,
    INDEX_NAME, EMBED_MODEL, LLM_MODEL,
)

logger = logging.getLogger(__name__)

# ─── Together AI client (OpenAI-compatible) ────────────────────
openai_client = OpenAI(
    api_key=TOGETHER_API_KEY,
    base_url=TOGETHER_BASE_URL,
)

# ─── Endee vector DB client ────────────────────────────────────
endee_client = Endee(ENDEE_AUTH_TOKEN) if ENDEE_AUTH_TOKEN else Endee()
endee_client.set_base_url(ENDEE_URL)

# ─── System Prompts per Query Mode ─────────────────────────────
SYSTEM_PROMPTS = {
    "explain": (
        "You are a senior software engineer. Explain this codebase's architecture "
        "clearly and thoroughly. Reference specific file names, function names, and "
        "class names from the provided code context. Describe how components interact, "
        "what patterns are used, and the overall design philosophy."
    ),
    "eli5": (
        "Explain this code like I'm 5 years old. Use simple analogies that a child "
        "would understand. Avoid technical jargon completely. Compare code concepts to "
        "everyday objects, stories, or games. Make it fun and engaging."
    ),
    "bugs": (
        "You are an expert code reviewer and security auditor. Carefully analyze the "
        "provided code snippets and identify:\n"
        "1. Potential bugs and logic errors\n"
        "2. Edge cases that aren't handled\n"
        "3. Security vulnerabilities\n"
        "4. Performance improvements\n"
        "5. Code quality suggestions\n"
        "Be specific — reference exact function names, line numbers, and provide "
        "concrete fix suggestions."
    ),
    "search": (
        "You are a precise code search engine. Find and explain the most relevant code "
        "for the user's query. Present the matching functions and classes with clear "
        "explanations of what they do, how they work, and how they relate to the query. "
        "Include file paths and line numbers."
    ),
}


def embed_query(text: str) -> List[float]:
    """
    Generate embedding for a query string using Together AI.
    Truncates to stay within the 512-token model limit.
    """
    response = openai_client.embeddings.create(
        model=EMBED_MODEL,
        input=text[:1500],  # ~512 tokens max
    )
    return response.data[0].embedding


def search_endee(query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Search Endee vector database for the most semantically similar code chunks.
    Returns top-k results with metadata and similarity scores.
    """
    try:
        index = endee_client.get_index(name=INDEX_NAME)
        results = index.query(
            vector=query_embedding,
            top_k=top_k,
            include_vectors=False,
        )
        return results if results else []
    except Exception as e:
        logger.error(f"Endee search failed: {e}")
        return []


def build_context(results: List[Dict[str, Any]]) -> str:
    """
    Build a context string from Endee search results for the LLM prompt.
    Formats each code chunk with file, line, and similarity info.
    """
    if not results:
        return "No relevant code chunks found in the index."

    context_parts = []
    for i, result in enumerate(results, 1):
        meta = result.get("meta", {})
        similarity = result.get("similarity", 0)
        name = meta.get("name", "unknown")
        chunk_type = meta.get("type", "unknown")
        file_path = meta.get("file", "unknown")
        start_line = meta.get("start_line", "?")
        end_line = meta.get("end_line", "?")
        code = meta.get("code", "")

        context_parts.append(
            f"--- Chunk {i} [{chunk_type}: {name}] "
            f"(file: {file_path}, lines {start_line}-{end_line}, "
            f"similarity: {similarity:.3f}) ---\n"
            f"{code}\n"
        )

    return "\n".join(context_parts)


def format_sources(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Format Endee results into a clean sources list for the frontend.
    """
    sources = []
    for result in results:
        meta = result.get("meta", {})
        sources.append({
            "name": meta.get("name", "unknown"),
            "type": meta.get("type", "unknown"),
            "file": meta.get("file", "unknown"),
            "start_line": meta.get("start_line", 0),
            "end_line": meta.get("end_line", 0),
            "language": meta.get("language", "unknown"),
            "similarity": round(result.get("similarity", 0), 4),
        })
    return sources


def ask_question(question: str, mode: str = "explain") -> Dict[str, Any]:
    """
    Full RAG pipeline:
    1. Embed the user's question
    2. Query Endee for top-5 similar code chunks
    3. Build a prompt with code context
    4. Call Together AI LLM for the answer
    5. Return the answer with source citations

    Modes: 'explain' | 'eli5' | 'bugs' | 'search'
    """
    # Validate mode
    if mode not in SYSTEM_PROMPTS:
        mode = "explain"

    try:
        # Step 1: Embed the question
        query_embedding = embed_query(question)

        # Step 2: Retrieve relevant code from Endee
        results = search_endee(query_embedding, top_k=5)

        if not results:
            return {
                "answer": "⚠️ No code has been indexed yet. Please ingest a repository first using the 'Ingest Repo' button.",
                "sources": [],
                "mode": mode,
            }

        # Step 3: Build context from retrieved chunks
        context = build_context(results)

        # Step 4: Call Together AI LLM via OpenAI-compatible API
        system_prompt = SYSTEM_PROMPTS[mode]
        user_prompt = (
            f"Based on the following code context from the repository, "
            f"answer the user's question.\n\n"
            f"=== CODE CONTEXT ===\n{context}\n\n"
            f"=== USER QUESTION ===\n{question}\n\n"
            f"Provide a detailed, well-structured answer. Reference specific "
            f"file names and function names when applicable."
        )

        completion = openai_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2048,
        )

        answer = completion.choices[0].message.content

        # Step 5: Format sources
        sources = format_sources(results)

        return {
            "answer": answer,
            "sources": sources,
            "mode": mode,
            "chunks_retrieved": len(results),
        }

    except Exception as e:
        logger.error(f"RAG pipeline failed: {e}")
        return {
            "answer": f"❌ Error processing your question: {str(e)}",
            "sources": [],
            "mode": mode,
        }
