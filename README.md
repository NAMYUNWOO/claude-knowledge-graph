# claude-knowledge-graph

Auto-capture Claude Code Q&A → Qwen 3.5 tagging/summarization → Obsidian knowledge graph

Automatically captures all conversations from Claude Code, tags and summarizes them with a local LLM (Qwen 3.5 4B), and builds a knowledge graph in your Obsidian vault.

![Knowledge Graph Example](docs/knowledge-graph-example.png)

## Why?

- **Knowledge disappears after every session** — Debugging insights, architecture decisions, and problem-solving patterns from Claude Code vanish when the session ends. This tool automatically turns them into searchable, structured notes.
- **Zero friction** — Hook-based auto-capture means you don't have to do anything. Just use Claude Code as usual.
- **Fast & lightweight edge AI** — Qwen 3.5 4B runs in the background and finishes tagging in seconds, never interrupting your workflow.
- **Privacy-safe** — All processing stays on your machine. Safe for enterprise environments where code-related conversations must not leave the local network.
- **Unified work archive** — No matter which project or directory you're working in, everything converges into a single Obsidian vault. Search and reflect on your entire work history in one place.
- **Connected knowledge graph** — Concepts are linked via wikilinks and shared tags, so you can visually explore how your technical topics relate to each other over time.

## Key Features

- **Auto-capture**: Collects all Q&A pairs automatically via Claude Code Hooks
- **Local LLM tagging**: llama.cpp + Qwen 3.5 4B GGUF (~2.5GB VRAM)
- **Auto-trigger**: Background tagging → note generation on Stop hook
- **Obsidian knowledge graph**: Auto-generates Daily notes, Concept notes, and MOC
- **Auto-linking between concepts**: Wikilinks based on co-occurrence + shared tags

## How It Works

```
Claude Code session
  │
  ├─ UserPromptSubmit hook → capture prompt
  └─ Stop hook → generate Q&A pair → background tagging
                    │
                    ▼
            Qwen 3.5 4B (via llama-server)
            → title, summary, tags, concepts
                    │
                    ▼
            Obsidian vault/knowledge-graph/
            ├── _MOC.md              (Map of Content)
            ├── daily/YYYY-MM-DD.md  (daily conversation log)
            └── concepts/*.md        (concept notes, wikilink connected)
```

## Installation

> **macOS (Apple Silicon) detailed guide**: [docs/install-macos-apple-silicon.md](docs/install-macos-apple-silicon.md)

### 1. Install the package

```bash
git clone https://github.com/NAMYUNWOO/claude-knowledge-graph.git
cd claude-knowledge-graph
pip install -e .
```

### 2. Install llama-server

```bash
# macOS (Homebrew)
brew install llama.cpp

# macOS (build from source — auto-detects Metal)
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DBUILD_SHARED_LIBS=OFF
cmake --build build --config Release -j$(sysctl -n hw.ncpu) --target llama-server

# Linux (CUDA)
git clone https://github.com/ggml-org/llama.cpp
cmake llama.cpp -B llama.cpp/build \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_CUDA=ON \
    -DCMAKE_CUDA_ARCHITECTURES="89"
cmake --build llama.cpp/build --config Release -j$(nproc) --target llama-server
```

> `CMAKE_CUDA_ARCHITECTURES`: RTX 40xx=89, RTX 30xx=86, RTX 20xx=75
>
> If `llama-server` is not on your PATH after building from source, specify the path directly in config.json (see Configuration below).

### 3. Download the GGUF model (~2.6 GB)

```bash
pip install huggingface-hub
huggingface-cli download unsloth/Qwen3.5-4B-GGUF \
  --include "*Q4_K_M*" \
  --local-dir ~/.local/share/claude-knowledge-graph/models/Qwen3.5-4B-GGUF
```

| Model | Size | VRAM | Recommended Environment |
|-------|------|------|-------------------------|
| Qwen3.5-4B Q4_K_M | ~2.6GB | ~3GB | 16GB RAM Mac, 4GB+ VRAM GPU |
| Qwen3.5-9B Q4_K_XL | ~5.6GB | ~6.5GB | 32GB+ RAM Mac, 8GB+ VRAM GPU |

### 4. Initialize

```bash
ckg init --vault-dir ~/my-obsidian-vault
```

This command will:
- Create `~/.config/claude-knowledge-graph/config.json`
- Create `~/.local/share/claude-knowledge-graph/{queue,processed,logs}` directories
- Auto-register hooks in `~/.claude/settings.json`
- Verify llama-server and model paths

## Usage

After installation, **it works automatically whenever you use Claude Code**.

```bash
# Check status
ckg status

# Run pipeline manually (tagging + note generation)
ckg run

# Unregister hooks
ckg uninstall
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `ckg init --vault-dir <path>` | Create config + register hooks |
| `ckg run` | Run pipeline (Qwen tagging → Obsidian notes) |
| `ckg status` | Show pending/processed/written counts + hooks status |
| `ckg uninstall` | Unregister hooks + optionally delete config |

## Configuration

Config file: `~/.config/claude-knowledge-graph/config.json`

Data directory: `~/.local/share/claude-knowledge-graph/`

### config.json example

```json
{
  "vault_dir": "/path/to/your/Obsidian Vault",
  "llama_server": "/path/to/llama-server",
  "model_path": "/path/to/Qwen3.5-4B-Q4_K_M.gguf",
  "llama_port": 8199
}
```

`vault_dir` is set automatically by `ckg init`. The rest only need to be specified if auto-detection fails.

### Environment Variables

Environment variables take priority over config.json.

| Variable | Description | Default |
|----------|-------------|---------|
| `CKG_VAULT_DIR` | Obsidian vault path | Read from config file |
| `CKG_LLAMA_SERVER` | llama-server binary path | Search PATH |
| `CKG_MODEL_PATH` | GGUF model file path | Search data dir |
| `CKG_LLAMA_PORT` | llama-server port | `8199` |

## Output Structure

```
your-vault/knowledge-graph/
├── _MOC.md                    # Map of Content (full index)
├── daily/
│   ├── 2026-03-10.md          # Daily conversation log
│   └── 2026-03-11.md
└── concepts/
    ├── Python Virtual Environments.md  # Concept note (wikilink connected)
    ├── Docker.md
    └── REST API.md
```

Each concept note is connected to related concepts via wikilinks, allowing you to visually explore the knowledge graph in Obsidian's graph view.

## Optional: Graph Visualization

```bash
pip install claude-knowledge-graph[graph]
python scripts/gen_graph_image.py
```

## Requirements

- Python 3.10+
- [Claude Code](https://claude.com/claude-code) CLI
- llama.cpp (`llama-server`)
- GPU recommended (CPU works but is slower)

## Performance

| Item | Value |
|------|-------|
| llama-server startup | ~7s |
| Tagging per file | 2-4s |
| VRAM usage | ~2.5GB (Q4_K_M) |
| Note generation | <1s |

## License

MIT
