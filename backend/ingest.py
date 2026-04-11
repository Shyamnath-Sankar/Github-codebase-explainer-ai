"""
Ingestion pipeline for GitHub Codebase Explainer.
Clones a GitHub repo → parses ALL code files → embeds code chunks →
upserts vectors to Endee vector database.

Supported languages: Python, JavaScript, TypeScript, Java, Go, Rust,
C, C++, C#, Ruby, PHP, Swift, Kotlin, Scala, Dart, Lua, Shell, YAML,
JSON, HTML, CSS, SQL, R, Markdown, and more.
"""

import os
import re
import ast
import uuid
import shutil
import tempfile
import logging
from typing import List, Dict, Any

from git import Repo
from openai import OpenAI
from endee import Endee, Precision

from config import (
    TOGETHER_API_KEY, TOGETHER_BASE_URL, ENDEE_URL, ENDEE_AUTH_TOKEN,
    INDEX_NAME, EMBED_MODEL, EMBED_DIM,
    MAX_CHUNK_CHARS, MAX_META_CHARS, MAX_EMBED_CHARS, UPSERT_BATCH_SIZE,
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

# ─── Supported file extensions mapped to language names ────────
SUPPORTED_EXTENSIONS = {
    # Python
    ".py": "python",
    # JavaScript / TypeScript
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    # Java / Kotlin / Scala
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala",
    # Go
    ".go": "go",
    # Rust
    ".rs": "rust",
    # C / C++
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    # C#
    ".cs": "csharp",
    # Ruby
    ".rb": "ruby",
    # PHP
    ".php": "php",
    # Swift
    ".swift": "swift",
    # Dart (Flutter)
    ".dart": "dart",
    # R
    ".r": "r", ".R": "r",
    # Lua
    ".lua": "lua",
    # Shell / Bash
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    # Web
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "scss", ".less": "less",
    # Data / Config
    ".json": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".sql": "sql",
    # Docs
    ".md": "markdown", ".mdx": "markdown",
    ".txt": "text",
    # DevOps
    ".dockerfile": "dockerfile",
    ".tf": "terraform",
    ".proto": "protobuf",
    # Misc
    ".graphql": "graphql", ".gql": "graphql",
    ".vue": "vue",
    ".svelte": "svelte",
}

# Files named exactly these are also supported (no extension match)
SUPPORTED_FILENAMES = {
    "Dockerfile": "dockerfile",
    "Makefile": "makefile",
    "Rakefile": "ruby",
    "Gemfile": "ruby",
    "Vagrantfile": "ruby",
    "Procfile": "text",
    ".env.example": "env",
    "docker-compose.yml": "yaml",
    "docker-compose.yaml": "yaml",
}

# ─── Regex patterns for function/class extraction per language ─
# These capture common function/class definitions for smart chunking
FUNCTION_PATTERNS = {
    "python": r"^(?:async\s+)?(?:def|class)\s+(\w+)",
    "javascript": r"^(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|class\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s+)?\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function)",
    "typescript": r"^(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*[=:]\s*(?:async\s+)?\(|interface\s+(\w+)|type\s+(\w+))",
    "java": r"^(?:public|private|protected|static|\s)*(?:class|interface|enum|void|int|String|boolean|long|double|float|char|byte)\s+(\w+)",
    "go": r"^(?:func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)|type\s+(\w+)\s+(?:struct|interface))",
    "rust": r"^(?:pub\s+)?(?:fn\s+(\w+)|struct\s+(\w+)|enum\s+(\w+)|impl\s+(\w+)|trait\s+(\w+))",
    "c": r"^(?:\w+[\s\*]+)(\w+)\s*\(|^(?:struct|enum|typedef)\s+(\w+)",
    "cpp": r"^(?:[\w:]+[\s\*&]+)?(\w+)(?:::\w+)?\s*\(|^(?:class|struct|enum|namespace)\s+(\w+)",
    "csharp": r"^(?:public|private|protected|internal|static|\s)*(?:class|interface|struct|enum|void|int|string|bool|async)\s+(\w+)",
    "ruby": r"^(?:def\s+(\w+[?!]?)|class\s+(\w+)|module\s+(\w+))",
    "php": r"^(?:public|private|protected|static|\s)*function\s+(\w+)|^class\s+(\w+)",
    "swift": r"^(?:func\s+(\w+)|class\s+(\w+)|struct\s+(\w+)|enum\s+(\w+)|protocol\s+(\w+))",
    "kotlin": r"^(?:fun\s+(\w+)|class\s+(\w+)|interface\s+(\w+)|object\s+(\w+))",
    "dart": r"^(?:class\s+(\w+)|\w+\s+(\w+)\s*\()",
    "scala": r"^(?:def\s+(\w+)|class\s+(\w+)|object\s+(\w+)|trait\s+(\w+))",
}

# Directories to always skip during traversal
SKIP_DIRS = {
    '.git', '__pycache__', 'node_modules', 'venv', 'env', '.venv',
    '.env', '.tox', '.mypy_cache', '.pytest_cache', 'dist', 'build',
    '.next', '.nuxt', '.output', 'coverage', '.idea', '.vs',
    'vendor', 'target', 'bin', 'obj', '.dart_tool', '.gradle',
    'Pods', '.swiftpm', 'DerivedData',
}


def clone_repo(repo_url: str) -> str:
    """
    Clone a GitHub repository to a temporary directory.
    Returns the path to the cloned repo.
    """
    tmp_dir = tempfile.mkdtemp(prefix="codebase_")
    logger.info(f"Cloning {repo_url} into {tmp_dir}")
    Repo.clone_from(repo_url, tmp_dir, depth=1)  # shallow clone for speed
    return tmp_dir


# ─── Python AST parser (best quality for .py files) ───────────
def extract_python_chunks(file_path: str, relative_path: str) -> List[Dict[str, Any]]:
    """Parse a Python file using AST to extract functions and classes."""
    chunks = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()

        tree = ast.parse(source, filename=file_path)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"
                start_line = node.lineno
                end_line = node.end_lineno or start_line

                lines = source.splitlines()
                code_snippet = "\n".join(lines[start_line - 1: end_line])

                chunks.append({
                    "name": node.name,
                    "type": chunk_type,
                    "code": code_snippet[:MAX_CHUNK_CHARS],
                    "code_meta": code_snippet[:MAX_META_CHARS],
                    "file": relative_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "python",
                })

    except SyntaxError:
        # Fall back to generic chunking if AST fails
        return extract_generic_chunks(file_path, relative_path, "python")
    except Exception as e:
        logger.warning(f"Error parsing {file_path}: {e}")

    return chunks


# ─── Generic smart chunker (all other languages) ──────────────
def extract_generic_chunks(file_path: str, relative_path: str, language: str) -> List[Dict[str, Any]]:
    """
    Extract code chunks from any source file using regex-based
    function/class detection + intelligent line-based splitting.
    """
    chunks = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()

        if not source.strip():
            return chunks

        lines = source.splitlines()
        pattern = FUNCTION_PATTERNS.get(language)

        if pattern:
            # ── Regex-based extraction: find functions/classes ──
            found_chunks = find_definitions(lines, pattern, relative_path, language)
            if found_chunks:
                chunks.extend(found_chunks)
                return chunks

        # ── Fallback: split file into logical blocks ───────────
        # Split by blank-line-separated blocks or fixed-size windows
        chunks.extend(split_into_blocks(lines, relative_path, language))

    except Exception as e:
        logger.warning(f"Error processing {file_path}: {e}")

    return chunks


def find_definitions(lines: List[str], pattern: str, relative_path: str, language: str) -> List[Dict[str, Any]]:
    """Find function/class definitions using regex and extract surrounding code."""
    chunks = []
    compiled = re.compile(pattern, re.MULTILINE)
    total_lines = len(lines)

    # Find all definition start lines
    def_starts = []
    for i, line in enumerate(lines):
        match = compiled.match(line.strip())
        if match:
            # Get the first non-None group as the name
            name = next((g for g in match.groups() if g), f"block_{i+1}")
            chunk_type = "class" if "class" in line.lower().split("(")[0] else "function"
            def_starts.append((i, name, chunk_type))

    if not def_starts:
        return []

    # Extract code for each definition (from start to next definition or end)
    for idx, (start, name, chunk_type) in enumerate(def_starts):
        if idx + 1 < len(def_starts):
            end = def_starts[idx + 1][0] - 1
            # Trim trailing blank lines
            while end > start and not lines[end].strip():
                end -= 1
        else:
            end = min(start + 80, total_lines - 1)  # Cap at 80 lines for last block

        code_snippet = "\n".join(lines[start:end + 1])

        chunks.append({
            "name": name,
            "type": chunk_type,
            "code": code_snippet[:MAX_CHUNK_CHARS],
            "code_meta": code_snippet[:MAX_META_CHARS],
            "file": relative_path,
            "start_line": start + 1,
            "end_line": end + 1,
            "language": language,
        })

    return chunks


def split_into_blocks(lines: List[str], relative_path: str, language: str, block_size: int = 60) -> List[Dict[str, Any]]:
    """
    Split a file into logical blocks for embedding.
    Tries to break on blank lines; falls back to fixed-size windows.
    """
    chunks = []
    total_lines = len(lines)

    if total_lines == 0:
        return chunks

    # For small files, treat the whole file as one chunk
    if total_lines <= block_size:
        code = "\n".join(lines)
        if code.strip():
            filename = os.path.basename(relative_path)
            chunks.append({
                "name": filename,
                "type": "file",
                "code": code[:MAX_CHUNK_CHARS],
                "code_meta": code[:MAX_META_CHARS],
                "file": relative_path,
                "start_line": 1,
                "end_line": total_lines,
                "language": language,
            })
        return chunks

    # For larger files, split into overlapping blocks
    i = 0
    block_num = 1
    while i < total_lines:
        end = min(i + block_size, total_lines)

        # Try to break on a blank line near the end
        if end < total_lines:
            for j in range(end, max(i + block_size // 2, i), -1):
                if not lines[j - 1].strip():
                    end = j
                    break

        code = "\n".join(lines[i:end])
        if code.strip():
            filename = os.path.basename(relative_path)
            chunks.append({
                "name": f"{filename}_part{block_num}",
                "type": "block",
                "code": code[:MAX_CHUNK_CHARS],
                "code_meta": code[:MAX_META_CHARS],
                "file": relative_path,
                "start_line": i + 1,
                "end_line": end,
                "language": language,
            })
            block_num += 1

        i = end

    return chunks


def get_file_language(file_path: str) -> str | None:
    """Determine the language of a file by extension or filename."""
    filename = os.path.basename(file_path)

    # Check exact filename matches first
    if filename in SUPPORTED_FILENAMES:
        return SUPPORTED_FILENAMES[filename]

    # Check extension
    _, ext = os.path.splitext(filename)
    ext = ext.lower()
    return SUPPORTED_EXTENSIONS.get(ext)


# ─── Main file walker ─────────────────────────────────────────
def walk_code_files(repo_dir: str) -> List[Dict[str, Any]]:
    """
    Walk the cloned repo and extract code chunks from ALL supported file types.
    Uses AST for Python, regex-based extraction for other languages,
    and intelligent block splitting as fallback.
    """
    all_chunks = []
    files_processed = 0
    lang_stats = {}

    for root, dirs, files in os.walk(repo_dir):
        # Skip hidden directories and non-essential folders
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]

        for filename in files:
            full_path = os.path.join(root, filename)
            relative_path = os.path.relpath(full_path, repo_dir)

            # Skip very large files (> 500KB) and binary files
            try:
                file_size = os.path.getsize(full_path)
                if file_size > 500_000 or file_size == 0:
                    continue
            except OSError:
                continue

            # Determine language
            language = get_file_language(full_path)
            if not language:
                continue

            # Extract chunks based on language
            if language == "python":
                chunks = extract_python_chunks(full_path, relative_path)
            else:
                chunks = extract_generic_chunks(full_path, relative_path, language)

            if chunks:
                all_chunks.extend(chunks)
                files_processed += 1
                lang_stats[language] = lang_stats.get(language, 0) + len(chunks)

    # Log summary
    lang_summary = ", ".join(f"{lang}: {count}" for lang, count in sorted(lang_stats.items(), key=lambda x: -x[1]))
    logger.info(f"Processed {files_processed} files → {len(all_chunks)} chunks ({lang_summary})")
    return all_chunks


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Generate embeddings for a list of texts using Together AI (OpenAI-compatible API).
    Truncates each text to MAX_EMBED_CHARS to stay within the model's 512-token limit.
    """
    if not texts:
        return []

    # Truncate each text to fit within embedding model's token limit
    # ~4 chars per token, 512 token limit → ~1500 char safe max
    truncated = [t[:MAX_EMBED_CHARS] for t in texts]

    response = openai_client.embeddings.create(
        model=EMBED_MODEL,
        input=truncated,
    )

    embeddings = [item.embedding for item in response.data]
    return embeddings


def ensure_index_exists():
    """
    Create the Endee index if it doesn't already exist.
    Uses a try/except approach — attempt to create the index,
    and if it already exists (409 Conflict), just continue.
    """
    try:
        logger.info(f"Ensuring Endee index '{INDEX_NAME}' exists (dim={EMBED_DIM}, cosine)")
        endee_client.create_index(
            name=INDEX_NAME,
            dimension=EMBED_DIM,
            space_type="cosine",
            precision=Precision.FLOAT32,
        )
        logger.info(f"Created new index '{INDEX_NAME}'")
    except Exception as e:
        error_msg = str(e).lower()
        if "conflict" in error_msg or "already exists" in error_msg:
            logger.info(f"Index '{INDEX_NAME}' already exists — reusing it")
        else:
            logger.error(f"Error creating index: {e}")
            raise


def upsert_to_endee(chunks: List[Dict[str, Any]]) -> int:
    """
    Embed code chunks and upsert them into Endee in batches.
    Returns the total number of vectors upserted.
    """
    ensure_index_exists()
    index = endee_client.get_index(name=INDEX_NAME)

    total_upserted = 0

    # Process in batches of UPSERT_BATCH_SIZE
    for i in range(0, len(chunks), UPSERT_BATCH_SIZE):
        batch = chunks[i: i + UPSERT_BATCH_SIZE]

        # Prepare texts for embedding: combine name + type + language + code
        texts = [
            f"{c['language']} {c['type']} {c['name']} in {c['file']}:\n{c['code']}"
            for c in batch
        ]

        # Generate embeddings
        embeddings = embed_texts(texts)

        # Build upsert records
        records = []
        for chunk, embedding in zip(batch, embeddings):
            record = {
                "id": str(uuid.uuid4()),
                "vector": embedding,
                "meta": {
                    "name": chunk["name"],
                    "type": chunk["type"],
                    "file": chunk["file"],
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "code": chunk["code_meta"],
                    "language": chunk["language"],
                },
            }
            records.append(record)

        # Upsert to Endee
        index.upsert(records)
        total_upserted += len(records)
        logger.info(f"Upserted batch: {total_upserted}/{len(chunks)} vectors")

    return total_upserted


def ingest_repository(repo_url: str) -> Dict[str, Any]:
    """
    Full ingestion pipeline:
    1. Clone the GitHub repo
    2. Extract code chunks from ALL supported file types
    3. Embed and upsert to Endee

    Returns stats about the ingestion process.
    """
    tmp_dir = None
    try:
        # Step 1: Clone
        tmp_dir = clone_repo(repo_url)

        # Step 2: Parse ALL code files
        chunks = walk_code_files(tmp_dir)

        if not chunks:
            return {
                "status": "warning",
                "message": "No supported code files found in the repository.",
                "files_processed": 0,
                "chunks_indexed": 0,
            }

        # Step 3: Embed & Upsert
        total_upserted = upsert_to_endee(chunks)

        # Compute language breakdown
        lang_counts = {}
        for c in chunks:
            lang = c.get("language", "unknown")
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

        return {
            "status": "success",
            "message": f"Successfully indexed {total_upserted} code chunks from repository.",
            "repo_url": repo_url,
            "chunks_indexed": total_upserted,
            "languages": lang_counts,
            "sample_chunks": [
                {"name": c["name"], "type": c["type"], "file": c["file"], "language": c["language"]}
                for c in chunks[:8]
            ],
        }

    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        return {
            "status": "error",
            "message": str(e),
            "chunks_indexed": 0,
        }

    finally:
        # Cleanup temp directory
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
