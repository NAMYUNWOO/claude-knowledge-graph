# macOS (Apple Silicon) Installation Guide

This guide covers installing and running claude-knowledge-graph on Apple Silicon Macs (M1/M2/M3/M4).

## Prerequisites

- macOS 14+ (Sonoma or later recommended)
- Python 3.10+ (Anaconda, Homebrew, or system Python all work)
- [Claude Code](https://claude.com/claude-code) CLI installed
- [Obsidian](https://obsidian.md/) vault created

## 1. Install llama.cpp

### Option A: Homebrew (simple)

```bash
brew install llama.cpp
```

After installation, `llama-server` is automatically added to PATH.

### Option B: Build from source (latest version)

```bash
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DBUILD_SHARED_LIBS=OFF
cmake --build build --config Release -j$(sysctl -n hw.ncpu) --target llama-server
```

Build output: `build/bin/llama-server`

Metal is auto-detected on Apple Silicon, so no separate GPU flags are needed.

> If you build from source, `llama-server` won't be on PATH. You'll need to specify the path in config.json after `ckg init` (see Step 4).

## 2. Download the GGUF Model

```bash
pip install huggingface-hub

# Qwen 3.5 4B (Q4_K_M, ~2.6GB) — recommended
huggingface-cli download unsloth/Qwen3.5-4B-GGUF \
  --include "*Q4_K_M*" \
  --local-dir ~/.local/share/claude-knowledge-graph/models/Qwen3.5-4B-GGUF
```

If `huggingface-cli` is not on PATH:

```bash
python3 -c "
from huggingface_hub import hf_hub_download
hf_hub_download('unsloth/Qwen3.5-4B-GGUF',
                'Qwen3.5-4B-Q4_K_M.gguf',
                local_dir='$HOME/.local/share/claude-knowledge-graph/models/Qwen3.5-4B-GGUF')
"
```

### Model Selection Guide

| Model | Size | VRAM | 16GB Mac | 32GB+ Mac |
|-------|------|------|----------|-----------|
| Qwen3.5-4B Q4_K_M | ~2.6GB | ~3GB | Recommended | OK |
| Qwen3.5-9B Q4_K_XL | ~5.6GB | ~6.5GB | Possible (tight) | Recommended |

Apple Silicon uses unified memory, so the model + KV cache + OS must all fit within total memory.

## 3. Install Package + Initialize

```bash
# Install from source
git clone https://github.com/yunwoonam/claude-knowledge-graph.git
cd claude-knowledge-graph
pip install -e .

# Initialize (specify your Obsidian vault path)
ckg init --vault-dir "/path/to/your/Obsidian Vault"
```

What `ckg init` does:
- Creates `~/.config/claude-knowledge-graph/config.json`
- Creates `~/.local/share/claude-knowledge-graph/{queue,processed,logs}` directories
- Auto-registers hooks in `~/.claude/settings.json`
- Verifies llama-server and model paths

## 4. Configure config.json (if needed)

If `ckg init` can't auto-detect llama-server or the model, configure them manually.

```bash
# Config file location
~/.config/claude-knowledge-graph/config.json
```

```json
{
  "vault_dir": "/path/to/your/Obsidian Vault",
  "llama_server": "/path/to/llama-server",
  "model_path": "/path/to/Qwen3.5-4B-Q4_K_M.gguf"
}
```

You can also use environment variables (these take priority over config.json):

```bash
export CKG_LLAMA_SERVER=/path/to/llama-server
export CKG_MODEL_PATH=/path/to/model.gguf
```

## 5. Verify Setup

```bash
# Check hooks registration + pending count
ckg status
```

Example output:
```
Pending:   0
Processed: 0
Written:   0
Total:     0

Hooks: all registered
```

### End-to-End Test

1. Ask a simple question in Claude Code
2. Check that a JSON file was created in the queue:
   ```bash
   ls ~/.local/share/claude-knowledge-graph/queue/
   ```
3. Run the pipeline:
   ```bash
   ckg run
   ```
4. Verify notes were generated in your Obsidian vault:
   ```bash
   ls "/path/to/your/Obsidian Vault/knowledge-graph/daily/"
   ls "/path/to/your/Obsidian Vault/knowledge-graph/concepts/"
   ```

## Troubleshooting

### Hooks not working

Claude Code may not load your shell profile (`.zshrc`) when executing hooks. In this case, `python3` points to the system Python which can't find the package.

**Fix**: Change the hook command to use an absolute path in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "/opt/anaconda3/bin/python3 -m claude_knowledge_graph.qa_logger"
      }]
    }],
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "/opt/anaconda3/bin/python3 -m claude_knowledge_graph.qa_logger"
      }]
    }]
  }
}
```

Or modify `HOOK_COMMAND` in hooks.py and re-run `ckg init`.

### llama-server won't start

```bash
# Run directly to check for errors
/path/to/llama-server \
  -m /path/to/model.gguf \
  --port 8199 \
  -ngl 99 \
  -c 4096 \
  --chat-template-kwargs '{"enable_thinking": false}'
```

### Check logs

```bash
cat ~/.local/share/claude-knowledge-graph/logs/qa_logger.log
cat ~/.local/share/claude-knowledge-graph/logs/qwen_processor.log
```

## Uninstall

```bash
# Unregister hooks + delete config
ckg uninstall

# Remove package
pip uninstall claude-knowledge-graph

# Delete data (optional)
rm -rf ~/.local/share/claude-knowledge-graph
rm -rf ~/.config/claude-knowledge-graph
```
