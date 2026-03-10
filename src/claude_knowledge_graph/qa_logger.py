#!/usr/bin/env python3
"""Hook handler for Claude Code: captures Q&A pairs to queue.

Reads JSON from stdin, handles UserPromptSubmit and Stop events.
Designed to be fast (file I/O only, no blocking calls).

Usage as hook: python3 -m claude_knowledge_graph.qa_logger
"""

import json
import sys
import os
from datetime import datetime
from pathlib import Path

from claude_knowledge_graph.config import DATA_DIR, QUEUE_DIR, LOGS_DIR


def log(msg: str) -> None:
    """Append a log line with timestamp."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOGS_DIR / "qa_logger.log"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def handle_prompt_submit(data: dict) -> None:
    """Save prompt to a temporary file keyed by session_id."""
    session_id = data.get("session_id", "unknown")
    prompt = data.get("prompt", "")
    cwd = data.get("cwd", "")
    timestamp = datetime.now().isoformat()

    if not prompt.strip():
        log(f"Empty prompt from session {session_id}, skipping")
        return

    prompt_file = QUEUE_DIR / f"{session_id}_prompt.json"
    entry = {
        "session_id": session_id,
        "timestamp": timestamp,
        "cwd": cwd,
        "prompt": prompt,
    }

    # Append to list of prompts for this session
    existing = []
    if prompt_file.exists():
        try:
            existing = json.loads(prompt_file.read_text())
            if isinstance(existing, dict):
                existing = [existing]
        except (json.JSONDecodeError, Exception):
            existing = []

    existing.append(entry)
    prompt_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
    log(f"Saved prompt for session {session_id} (#{len(existing)})")


def extract_full_response(transcript_path: str) -> str:
    """Extract all assistant text messages from the transcript JSONL file.

    Concatenates all assistant text blocks (excluding tool calls) to capture
    intermediate explanations between tool uses.
    """
    if not transcript_path:
        return ""
    try:
        parts: list[str] = []
        with open(transcript_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("role") != "assistant":
                    continue
                content = entry.get("content", "")
                # content can be a string or a list of content blocks
                if isinstance(content, str):
                    if content.strip():
                        parts.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                parts.append(text)
                        elif isinstance(block, str) and block.strip():
                            parts.append(block.strip())
        return "\n\n".join(parts) if parts else ""
    except Exception as e:
        log(f"Failed to read transcript: {e}")
        return ""


def handle_stop(data: dict) -> None:
    """Merge last assistant message with most recent prompt, create Q&A pair."""
    session_id = data.get("session_id", "unknown")
    stop_hook_active = data.get("stop_hook_active", False)

    # Prevent infinite loops
    if stop_hook_active:
        log(f"stop_hook_active=True for session {session_id}, skipping")
        return

    # Try full transcript first, fall back to last_assistant_message
    transcript_path = data.get("transcript_path", "")
    response = extract_full_response(transcript_path)
    if not response:
        response = data.get("last_assistant_message", "")
    cwd = data.get("cwd", "")
    timestamp = datetime.now().isoformat()

    prompt_file = QUEUE_DIR / f"{session_id}_prompt.json"

    if not prompt_file.exists():
        log(f"No prompt file for session {session_id}, saving response-only")
        qa_entry = {
            "session_id": session_id,
            "timestamp": timestamp,
            "cwd": cwd,
            "prompt": "",
            "response": response,
            "status": "pending",
        }
        ts_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = QUEUE_DIR / f"{ts_slug}_{session_id}.json"
        out_file.write_text(json.dumps(qa_entry, ensure_ascii=False, indent=2))
        return

    # Read the most recent prompt
    try:
        prompts = json.loads(prompt_file.read_text())
        if isinstance(prompts, dict):
            prompts = [prompts]
    except (json.JSONDecodeError, Exception):
        log(f"Failed to read prompt file for session {session_id}")
        prompts = []

    if not prompts:
        log(f"Empty prompts list for session {session_id}")
        prompt_file.unlink(missing_ok=True)
        return

    # Take the last prompt entry
    last_prompt = prompts[-1]

    # Create the Q&A pair
    qa_entry = {
        "session_id": session_id,
        "timestamp": last_prompt.get("timestamp", timestamp),
        "cwd": last_prompt.get("cwd", cwd),
        "prompt": last_prompt.get("prompt", ""),
        "response": response,
        "status": "pending",
    }

    ts_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = QUEUE_DIR / f"{ts_slug}_{session_id}.json"
    out_file.write_text(json.dumps(qa_entry, ensure_ascii=False, indent=2))
    log(f"Created Q&A pair: {out_file.name}")

    # Clean up prompt file
    prompt_file.unlink(missing_ok=True)

    # Trigger background processing
    trigger_processor()


def trigger_processor() -> None:
    """Launch qwen_processor in background if not already running.

    Uses a lock file to prevent duplicate runs.
    """
    import subprocess
    import fcntl

    lock_file = DATA_DIR / "processor.lock"
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            log("Processor already running, skipping trigger")
            return

        # Release the lock — the subprocess will acquire its own
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

        # Launch processor in background
        subprocess.Popen(
            [sys.executable, "-m", "claude_knowledge_graph.qwen_processor"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log("Triggered background processor")
    except Exception as e:
        log(f"Failed to trigger processor: {e}")


def main() -> None:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        log(f"Failed to parse stdin: {e}")
        sys.exit(0)  # Exit 0 to not block Claude Code

    event = data.get("hook_event_name", "")
    log(f"Received event: {event} (session: {data.get('session_id', 'unknown')})")

    if event == "UserPromptSubmit":
        handle_prompt_submit(data)
    elif event == "Stop":
        handle_stop(data)
    else:
        log(f"Unknown event: {event}")

    # Always exit 0 to not interfere with Claude Code
    sys.exit(0)


if __name__ == "__main__":
    main()
