"""Configuration for claude-knowledge-graph.

Resolution order: environment variable → config file → default value.
Config file: ~/.config/claude-knowledge-graph/config.json
Data directory: ~/.local/share/claude-knowledge-graph/
"""

import json
import shutil
from pathlib import Path

# ── Config file path ──
CONFIG_DIR = Path.home() / ".config" / "claude-knowledge-graph"
CONFIG_FILE = CONFIG_DIR / "config.json"

# ── Data directory ──
DATA_DIR = Path.home() / ".local" / "share" / "claude-knowledge-graph"


def _load_config() -> dict:
    """Load config from JSON file if it exists."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _get(env_key: str, config_key: str, default: str | Path) -> str:
    """Resolve a config value: env var → config file → default."""
    import os

    val = os.environ.get(env_key)
    if val:
        return val
    cfg = _load_config()
    val = cfg.get(config_key)
    if val:
        return val
    return str(default)


# ── Pipeline directories ──
QUEUE_DIR = Path(_get("CKG_QUEUE_DIR", "queue_dir", DATA_DIR / "queue"))
PROCESSED_DIR = Path(_get("CKG_PROCESSED_DIR", "processed_dir", DATA_DIR / "processed"))
LOGS_DIR = Path(_get("CKG_LOGS_DIR", "logs_dir", DATA_DIR / "logs"))

# ── Obsidian vault ──
VAULT_DIR = Path(_get("CKG_VAULT_DIR", "vault_dir", ""))
KNOWLEDGE_GRAPH_DIR = VAULT_DIR / "knowledge-graph" if VAULT_DIR != Path("") else Path("")
DAILY_DIR = KNOWLEDGE_GRAPH_DIR / "daily"
CONCEPTS_DIR = KNOWLEDGE_GRAPH_DIR / "concepts"
SESSIONS_DIR = KNOWLEDGE_GRAPH_DIR / "sessions"
MOC_PATH = KNOWLEDGE_GRAPH_DIR / "_MOC.md"


def _find_llama_server() -> Path:
    """Find llama-server binary: env var → config → PATH."""
    import os

    env = os.environ.get("CKG_LLAMA_SERVER")
    if env:
        return Path(env)
    cfg = _load_config()
    if cfg.get("llama_server"):
        return Path(cfg["llama_server"])
    found = shutil.which("llama-server")
    if found:
        return Path(found)
    return Path("llama-server")  # will fail at runtime with clear error


def _find_gguf_model() -> Path:
    """Find GGUF model: env var → config → default data dir."""
    import os

    env = os.environ.get("CKG_MODEL_PATH")
    if env:
        return Path(env)
    cfg = _load_config()
    if cfg.get("model_path"):
        return Path(cfg["model_path"])
    # Search in default models directory
    models_dir = DATA_DIR / "models"
    if models_dir.exists():
        for gguf in models_dir.rglob("*Q4_K_M*.gguf"):
            return gguf
    return models_dir / "Qwen3.5-4B-GGUF" / "Qwen3.5-4B-Q4_K_M.gguf"


# ── llama.cpp + GGUF settings ──
LLAMA_SERVER_BIN = _find_llama_server()
GGUF_MODEL_PATH = _find_gguf_model()
LLAMA_PORT = int(_get("CKG_LLAMA_PORT", "llama_port", "8199"))
LLAMA_CTX_SIZE = 4096
LLAMA_TEMPERATURE = 0.7
LLAMA_TOP_P = 0.8
LLAMA_TOP_K = 20
LLAMA_MAX_TOKENS = 512

# ── Processing ──
MAX_PROMPT_CHARS = 3000
MAX_RESPONSE_CHARS = 3000
