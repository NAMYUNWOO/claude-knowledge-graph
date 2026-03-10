#!/usr/bin/env python3
"""On-demand Qwen 3.5 4B processor for Q&A tagging and summarization.

Uses llama-server (llama.cpp) with Unsloth GGUF model, accessed via OpenAI-compatible API.
"""

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from claude_knowledge_graph.config import (
    DATA_DIR,
    GGUF_MODEL_PATH,
    LLAMA_CTX_SIZE,
    LLAMA_MAX_TOKENS,
    LLAMA_PORT,
    LLAMA_SERVER_BIN,
    LLAMA_TEMPERATURE,
    LLAMA_TOP_K,
    LLAMA_TOP_P,
    LOGS_DIR,
    MAX_PROMPT_CHARS,
    MAX_RESPONSE_CHARS,
    PROCESSED_DIR,
    QUEUE_DIR,
)

LOG_FILE = LOGS_DIR / "qwen_processor.log"

# Global server process reference
_server_proc = None


def log(msg: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def start_server() -> subprocess.Popen:
    """Start llama-server and wait until it's ready."""
    global _server_proc
    if _server_proc is not None and _server_proc.poll() is None:
        return _server_proc

    server_bin = Path(LLAMA_SERVER_BIN)
    if not server_bin.exists() and not server_bin.is_absolute():
        # Try resolving from PATH (already done in config, but double-check)
        import shutil
        found = shutil.which(str(LLAMA_SERVER_BIN))
        if found:
            server_bin = Path(found)

    if not server_bin.exists():
        raise FileNotFoundError(
            f"llama-server not found: {LLAMA_SERVER_BIN}\n\n"
            "Install llama.cpp:\n"
            "  macOS:  brew install llama.cpp\n"
            "  Linux:  build from source (https://github.com/ggml-org/llama.cpp)\n\n"
            "Or set CKG_LLAMA_SERVER=/path/to/llama-server"
        )

    model_path = Path(GGUF_MODEL_PATH)
    if not model_path.exists():
        raise FileNotFoundError(
            f"GGUF model not found: {GGUF_MODEL_PATH}\n\n"
            "Download the model:\n"
            "  pip install huggingface-hub\n"
            "  huggingface-cli download unsloth/Qwen3.5-4B-GGUF \\\n"
            "    --include '*Q4_K_M*' \\\n"
            "    --local-dir ~/.local/share/claude-knowledge-graph/models/Qwen3.5-4B-GGUF\n\n"
            "Or set CKG_MODEL_PATH=/path/to/model.gguf"
        )

    cmd = [
        str(server_bin),
        "--model", str(model_path),
        "--port", str(LLAMA_PORT),
        "--ctx-size", str(LLAMA_CTX_SIZE),
        "--n-gpu-layers", "99",
        "--chat-template-kwargs", '{"enable_thinking": false}',
    ]
    log(f"Starting llama-server: {' '.join(cmd)}")

    server_log = LOGS_DIR / "llama_server.log"
    log_fh = open(server_log, "a")
    _server_proc = subprocess.Popen(
        cmd, stdout=log_fh, stderr=subprocess.STDOUT
    )

    # Wait for health endpoint
    import urllib.request
    import urllib.error

    health_url = f"http://127.0.0.1:{LLAMA_PORT}/health"
    for i in range(60):
        if _server_proc.poll() is not None:
            raise RuntimeError(
                f"llama-server exited with code {_server_proc.returncode}. "
                f"Check {server_log}"
            )
        try:
            resp = urllib.request.urlopen(health_url, timeout=2)
            if resp.status == 200:
                log(f"llama-server ready after {i + 1}s")
                return _server_proc
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)

    stop_server()
    raise TimeoutError("llama-server failed to start within 60s")


def stop_server() -> None:
    """Stop llama-server to free VRAM."""
    global _server_proc
    if _server_proc is None:
        return
    if _server_proc.poll() is None:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
            _server_proc.wait()
    _server_proc = None
    log("llama-server stopped, VRAM released")


def get_pending_files() -> list[Path]:
    """Get all pending Q&A pair files from the queue."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for f in sorted(QUEUE_DIR.glob("*.json")):
        if f.stem.endswith("_prompt"):
            continue
        try:
            data = json.loads(f.read_text())
            if data.get("status") == "pending":
                files.append(f)
        except (json.JSONDecodeError, Exception):
            continue
    return files


def build_tagging_prompt(qa: dict) -> str:
    """Build the prompt for Qwen to tag/summarize a Q&A pair."""
    prompt_text = qa.get("prompt", "")[:MAX_PROMPT_CHARS]
    response_text = qa.get("response", "")[:MAX_RESPONSE_CHARS]

    return f"""Analyze this developer Q&A pair and respond with JSON only.

Question: {prompt_text}

Answer: {response_text}

Respond with this exact JSON structure:
{{
  "title": "Short descriptive title (under 10 words)",
  "summary": "2-3 sentence summary of what was discussed and resolved",
  "tags": ["lowercase-kebab-case", "3-to-6-tags"],
  "category": "one of: development | debugging | architecture | devops | data | testing | tooling | other",
  "key_concepts": ["Specific Technical Concept", "2-to-5-concepts"]
}}

Guidelines:
- key_concepts: Title Case. Be specific (e.g. "Python Virtual Environments" not "Python"). Think of them as encyclopedia article titles that can be reused across conversations.
- tags: lowercase kebab-case. Include the primary language/framework and problem domain. Aim for 3-6 tags.
- category: Choose the single best fit."""


def extract_json(text: str) -> dict | None:
    """Extract JSON from model output, handling code blocks and think tags."""
    text = text.strip()

    # Remove <think>...</think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Try direct JSON parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Extract from markdown code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try to find JSON object pattern
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def call_qwen(prompt: str) -> dict | None:
    """Call Qwen via llama-server's OpenAI-compatible API."""
    from openai import OpenAI

    client = OpenAI(
        base_url=f"http://127.0.0.1:{LLAMA_PORT}/v1",
        api_key="not-needed",
    )

    messages = [
        {
            "role": "system",
            "content": "You are a knowledge graph extraction engine. You analyze developer Q&A conversations and output structured JSON. key_concepts become graph nodes and tags drive relationship linking. Output only valid JSON.",
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response = client.chat.completions.create(
            model="qwen",
            messages=messages,
            max_tokens=LLAMA_MAX_TOKENS,
            temperature=LLAMA_TEMPERATURE,
            top_p=LLAMA_TOP_P,
            extra_body={"top_k": LLAMA_TOP_K},
        )
        content = response.choices[0].message.content or ""
        log(f"Raw output length: {len(content)} chars")

        parsed = extract_json(content)
        if parsed is None:
            log(f"Failed to parse JSON from output: {content[:200]}...")
        return parsed
    except Exception as e:
        log(f"Qwen inference failed: {e}")
        return None


def process_file(filepath: Path) -> bool:
    """Process a single Q&A pair file."""
    try:
        qa = json.loads(filepath.read_text())
    except (json.JSONDecodeError, Exception) as e:
        log(f"Failed to read {filepath.name}: {e}")
        return False

    prompt = build_tagging_prompt(qa)
    result = call_qwen(prompt)

    if result is None:
        log(f"Failed to get Qwen result for {filepath.name}")
        return False

    # Merge Qwen results into the Q&A entry
    qa["qwen_result"] = result
    qa["status"] = "processed"
    qa["processed_at"] = datetime.now().isoformat()

    # Save to processed directory
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_file = PROCESSED_DIR / filepath.name
    out_file.write_text(json.dumps(qa, ensure_ascii=False, indent=2))

    # Remove from queue
    filepath.unlink()
    log(f"Processed: {filepath.name} → {out_file.name}")
    return True


ORPHAN_MAX_AGE_SECONDS = 3600  # 1 hour


def cleanup_orphan_prompts() -> None:
    """Remove _prompt.json files older than ORPHAN_MAX_AGE_SECONDS."""
    now = time.time()
    removed = 0
    for f in QUEUE_DIR.glob("*_prompt.json"):
        age = now - f.stat().st_mtime
        if age > ORPHAN_MAX_AGE_SECONDS:
            f.unlink()
            removed += 1
            log(f"Removed orphan prompt ({age / 3600:.1f}h old): {f.name}")
    if removed:
        log(f"Cleaned up {removed} orphan prompt file(s)")


def main() -> None:
    import fcntl

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    lock_file = DATA_DIR / "processor.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    # Acquire exclusive lock to prevent duplicate runs
    lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("Another processor instance is running, exiting")
        lock_fd.close()
        return

    try:
        log("=" * 50)
        log("Qwen Processor started (llama.cpp server mode)")

        cleanup_orphan_prompts()

        pending = get_pending_files()
        if not pending:
            log("No pending files in queue, exiting")
            return

        log(f"Found {len(pending)} pending file(s)")

        try:
            start_server()

            success = 0
            fail = 0
            for f in pending:
                log(f"Processing: {f.name}")
                if process_file(f):
                    success += 1
                else:
                    fail += 1

            log(f"Processing complete: {success} success, {fail} failed")
        finally:
            stop_server()

        log("Qwen Processor finished")

        # Run obsidian_writer to generate knowledge graph notes
        if success > 0:
            try:
                from claude_knowledge_graph.obsidian_writer import main as write_obsidian
                write_obsidian()
            except Exception as e:
                log(f"Obsidian writer failed: {e}")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    main()
