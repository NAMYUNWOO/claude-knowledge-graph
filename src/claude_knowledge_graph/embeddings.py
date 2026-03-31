#!/usr/bin/env python3
"""Embedding engine using a dedicated GGUF embedding model via llama-server.

Manages a separate llama-server instance for embeddings (different port from tagging).
Provides embedding generation, caching, and cosine similarity computation.
"""

import hashlib
import json
import math
import subprocess
import time
from datetime import datetime
from pathlib import Path

from claude_knowledge_graph.config import (
    DATA_DIR,
    EMBED_MODEL_PATH,
    EMBED_PORT,
    EMBEDDINGS_INDEX_PATH,
    LLAMA_SERVER_BIN,
    LOGS_DIR,
    MAX_PROMPT_CHARS,
    MAX_RESPONSE_CHARS,
)

LOG_FILE = LOGS_DIR / "embeddings.log"

_embed_server_proc = None


def log(msg: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def is_configured() -> bool:
    """Check if embedding model is configured and exists."""
    return EMBED_MODEL_PATH != Path("") and EMBED_MODEL_PATH.exists()


def start_embed_server() -> subprocess.Popen:
    """Start llama-server with embedding model on the embed port."""
    global _embed_server_proc
    if _embed_server_proc is not None and _embed_server_proc.poll() is None:
        return _embed_server_proc

    if not is_configured():
        raise FileNotFoundError(
            f"Embedding model not found: {EMBED_MODEL_PATH}\n\n"
            "Download an embedding model:\n"
            "  pip install huggingface-hub\n"
            "  huggingface-cli download Qwen/Qwen3-Embedding-0.6B-GGUF \\\n"
            "    --include '*q8_0*' \\\n"
            "    --local-dir ~/.local/share/claude-knowledge-graph/models/Qwen3-Embedding-0.6B-GGUF\n\n"
            "Or set CKG_EMBED_MODEL_PATH=/path/to/embedding-model.gguf"
        )

    import shutil

    server_bin = Path(LLAMA_SERVER_BIN)
    if not server_bin.exists():
        found = shutil.which(str(LLAMA_SERVER_BIN))
        if found:
            server_bin = Path(found)

    cmd = [
        str(server_bin),
        "--model", str(EMBED_MODEL_PATH),
        "--port", str(EMBED_PORT),
        "--ctx-size", "8192",
        "--n-gpu-layers", "99",
        "--embeddings",
    ]
    log(f"Starting embedding server: {' '.join(cmd)}")

    server_log = LOGS_DIR / "embed_server.log"
    log_fh = open(server_log, "a")
    _embed_server_proc = subprocess.Popen(
        cmd, stdout=log_fh, stderr=subprocess.STDOUT
    )

    # Wait for health
    import urllib.error
    import urllib.request

    health_url = f"http://127.0.0.1:{EMBED_PORT}/health"
    for i in range(60):
        if _embed_server_proc.poll() is not None:
            raise RuntimeError(
                f"Embedding server exited with code {_embed_server_proc.returncode}. "
                f"Check {server_log}"
            )
        try:
            resp = urllib.request.urlopen(health_url, timeout=2)
            if resp.status == 200:
                log(f"Embedding server ready after {i + 1}s")
                return _embed_server_proc
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)

    stop_embed_server()
    raise TimeoutError("Embedding server failed to start within 60s")


def stop_embed_server() -> None:
    """Stop embedding server to free VRAM."""
    global _embed_server_proc
    if _embed_server_proc is None:
        return
    if _embed_server_proc.poll() is None:
        _embed_server_proc.terminate()
        try:
            _embed_server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _embed_server_proc.kill()
            _embed_server_proc.wait()
    _embed_server_proc = None
    log("Embedding server stopped, VRAM released")


def get_embedding(text: str) -> list[float]:
    """Get embedding vector for a text string via /v1/embeddings endpoint."""
    from openai import OpenAI

    client = OpenAI(
        base_url=f"http://127.0.0.1:{EMBED_PORT}/v1",
        api_key="not-needed",
    )

    # Truncate text to avoid context overflow
    text = text[:4000]

    response = client.embeddings.create(
        model="embedding",
        input=text,
    )
    return response.data[0].embedding


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embedding vectors for multiple texts in a single call."""
    from openai import OpenAI

    client = OpenAI(
        base_url=f"http://127.0.0.1:{EMBED_PORT}/v1",
        api_key="not-needed",
    )

    truncated = [t[:4000] for t in texts]
    response = client.embeddings.create(
        model="embedding",
        input=truncated,
    )
    # Sort by index to maintain order
    sorted_data = sorted(response.data, key=lambda x: x.index)
    return [d.embedding for d in sorted_data]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def text_hash(text: str) -> str:
    """Compute SHA256 hash of text for change detection."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def qa_to_embed_text(qa: dict) -> str:
    """Convert a Q&A pair to text suitable for embedding."""
    qwen = qa.get("qwen_result", {})
    title = qwen.get("title", "")
    summary = qwen.get("summary", "")
    tags = " ".join(qwen.get("tags", []))
    concepts = " ".join(qwen.get("key_concepts", []))
    prompt = qa.get("prompt", "")[:MAX_PROMPT_CHARS]
    response = qa.get("response", "")[:MAX_RESPONSE_CHARS]

    return f"{title}\n{summary}\n{tags} {concepts}\n{prompt}\n{response}"


def load_index() -> dict:
    """Load the embeddings index from disk."""
    if EMBEDDINGS_INDEX_PATH.exists():
        try:
            return json.loads(EMBEDDINGS_INDEX_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"model": "", "dimensions": 0, "entries": {}}


def save_index(index: dict) -> None:
    """Save the embeddings index to disk."""
    EMBEDDINGS_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    EMBEDDINGS_INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False))
    log(f"Saved embeddings index: {len(index.get('entries', {}))} entries")


def build_embeddings_for_qas(
    qa_files: list[tuple[str, dict]],
) -> int:
    """Generate embeddings for Q&A pairs that need them.

    Args:
        qa_files: list of (file_id, qa_dict) tuples

    Returns: number of new embeddings generated.
    """
    index = load_index()
    entries = index.get("entries", {})

    # Find Q&As that need embedding
    to_embed: list[tuple[str, dict, str]] = []
    for file_id, qa in qa_files:
        embed_text = qa_to_embed_text(qa)
        t_hash = text_hash(embed_text)

        existing = entries.get(file_id)
        if existing and existing.get("text_hash") == t_hash:
            continue  # Already embedded, no changes

        to_embed.append((file_id, qa, embed_text))

    if not to_embed:
        log("All Q&As already embedded, skipping")
        return 0

    log(f"Generating embeddings for {len(to_embed)} Q&A pairs")

    # Batch embed
    texts = [t for _, _, t in to_embed]
    try:
        embeddings = get_embeddings_batch(texts)
    except Exception as e:
        log(f"Batch embedding failed, falling back to individual: {e}")
        embeddings = []
        for text in texts:
            try:
                embeddings.append(get_embedding(text))
            except Exception as e2:
                log(f"Individual embedding failed: {e2}")
                embeddings.append([])

    # Update index
    for i, (file_id, qa, embed_text) in enumerate(to_embed):
        if i < len(embeddings) and embeddings[i]:
            entries[file_id] = {
                "embedding": embeddings[i],
                "text_hash": text_hash(embed_text),
                "created_at": datetime.now().isoformat(),
            }
            if not index.get("dimensions"):
                index["dimensions"] = len(embeddings[i])

    index["model"] = EMBED_MODEL_PATH.stem if EMBED_MODEL_PATH != Path("") else "unknown"
    index["entries"] = entries
    save_index(index)

    return len(to_embed)
