#!/usr/bin/env python3
"""CLI for claude-knowledge-graph.

Commands:
    ckg init --vault-dir <path>   Initialize config, directories, and hooks
    ckg run                       Run the pipeline (tagging + note generation)
    ckg status                    Show pending/processed/written counts
    ckg uninstall                 Remove hooks and clean up config
"""

import json
import sys
from pathlib import Path

import click

from claude_knowledge_graph.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DATA_DIR,
    LOGS_DIR,
    PROCESSED_DIR,
    QUEUE_DIR,
)


@click.group()
@click.version_option(package_name="claude-knowledge-graph")
def main():
    """Auto-capture Claude Code Q&A → Qwen tagging → Obsidian knowledge graph."""
    pass


@main.command()
@click.option(
    "--vault-dir",
    required=True,
    type=click.Path(exists=False),
    help="Path to your Obsidian vault directory.",
)
def init(vault_dir: str):
    """Initialize config, create directories, and register Claude Code hooks."""
    vault_path = Path(vault_dir).expanduser().resolve()

    # 1. Create config file
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    config["vault_dir"] = str(vault_path)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    click.echo(f"Config saved: {CONFIG_FILE}")

    # 2. Create data directories
    for d in [QUEUE_DIR, PROCESSED_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    click.echo(f"Data directory: {DATA_DIR}")

    # 3. Create vault knowledge-graph directory
    kg_dir = vault_path / "knowledge-graph"
    for sub in ["daily", "concepts", "sessions"]:
        (kg_dir / sub).mkdir(parents=True, exist_ok=True)
    click.echo(f"Knowledge graph directory: {kg_dir}")

    # 4. Register hooks
    from claude_knowledge_graph.hooks import register_hooks

    if register_hooks():
        click.echo("Claude Code hooks registered.")
    else:
        click.echo("Claude Code hooks already registered.")

    # 5. Check dependencies
    click.echo("")
    _check_dependencies()

    click.echo("")
    click.echo("Setup complete! Claude Code will now capture Q&A pairs automatically.")
    click.echo("Run 'ckg run' to process pending entries, or they'll be processed automatically.")


def _check_dependencies():
    """Check if llama-server and model are available, prompt if not found."""
    import shutil

    from claude_knowledge_graph.config import GGUF_MODEL_PATH, LLAMA_SERVER_BIN

    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    config_changed = False

    # Check llama-server
    server_path = Path(LLAMA_SERVER_BIN)
    if server_path.exists() or shutil.which(str(LLAMA_SERVER_BIN)):
        click.echo(f"llama-server: found ({LLAMA_SERVER_BIN})")
    else:
        click.echo("llama-server: NOT FOUND (auto-detect failed)")
        user_path = click.prompt(
            "  Enter llama-server path (or press Enter to skip)",
            default="",
            show_default=False,
        )
        if user_path:
            user_path = str(Path(user_path).expanduser().resolve())
            if Path(user_path).exists():
                config["llama_server"] = user_path
                config_changed = True
                click.echo(f"  llama-server: saved ({user_path})")
            else:
                click.echo(f"  Warning: {user_path} does not exist, skipping.")
                click.echo("  Install: brew install llama.cpp  (macOS)")
                click.echo("  Or build from source: https://github.com/ggml-org/llama.cpp")
        else:
            click.echo("  Install: brew install llama.cpp  (macOS)")
            click.echo("  Or build from source: https://github.com/ggml-org/llama.cpp")

    # Check model
    model_path = Path(GGUF_MODEL_PATH)
    if model_path.exists():
        size_mb = model_path.stat().st_size / (1024 * 1024)
        click.echo(f"Model: found ({model_path.name}, {size_mb:.0f} MB)")
    else:
        click.echo("Model: NOT FOUND (auto-detect failed)")
        user_path = click.prompt(
            "  Enter GGUF model path (or press Enter to skip)",
            default="",
            show_default=False,
        )
        if user_path:
            user_path = str(Path(user_path).expanduser().resolve())
            if Path(user_path).exists():
                config["model_path"] = user_path
                config_changed = True
                click.echo(f"  Model: saved ({Path(user_path).name})")
            else:
                click.echo(f"  Warning: {user_path} does not exist, skipping.")
                _echo_model_download_help()
        else:
            _echo_model_download_help()

    if config_changed:
        CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")


def _echo_model_download_help():
    """Print model download instructions."""
    click.echo("  Download:")
    click.echo("    pip install huggingface-hub")
    click.echo("    huggingface-cli download unsloth/Qwen3.5-4B-GGUF \\")
    click.echo("      --include '*Q4_K_M*' \\")
    click.echo(f"      --local-dir {DATA_DIR / 'models' / 'Qwen3.5-4B-GGUF'}")


@main.command()
def run():
    """Run the pipeline: Qwen tagging → Obsidian note generation."""
    from claude_knowledge_graph.qwen_processor import main as run_processor

    run_processor()


@main.command()
def status():
    """Show counts of pending, processed, and written Q&A pairs."""
    counts = {"pending": 0, "processed": 0, "written": 0}

    for directory, expected_status in [(QUEUE_DIR, "pending"), (PROCESSED_DIR, None)]:
        if not directory.exists():
            continue
        for f in directory.glob("*.json"):
            if f.stem.endswith("_prompt"):
                continue
            try:
                data = json.loads(f.read_text())
                s = data.get("status", "unknown")
                if s in counts:
                    counts[s] += 1
            except (json.JSONDecodeError, OSError):
                continue

    click.echo(f"Pending:   {counts['pending']}")
    click.echo(f"Processed: {counts['processed']}")
    click.echo(f"Written:   {counts['written']}")
    click.echo(f"Total:     {sum(counts.values())}")

    # Show hooks status
    click.echo("")
    from claude_knowledge_graph.hooks import check_hooks

    hook_status = check_hooks()
    all_ok = all(hook_status.values())
    click.echo(f"Hooks: {'all registered' if all_ok else 'MISSING'}")
    if not all_ok:
        for event, registered in hook_status.items():
            if not registered:
                click.echo(f"  {event}: not registered (run 'ckg init' to fix)")


@main.command()
def uninstall():
    """Remove Claude Code hooks and clean up config."""
    from claude_knowledge_graph.hooks import unregister_hooks

    if unregister_hooks():
        click.echo("Claude Code hooks removed.")
    else:
        click.echo("No hooks to remove.")

    # Ask before removing config
    if CONFIG_FILE.exists():
        if click.confirm("Remove config file?", default=False):
            CONFIG_FILE.unlink()
            click.echo(f"Removed: {CONFIG_FILE}")
            # Remove config dir if empty
            try:
                CONFIG_DIR.rmdir()
            except OSError:
                pass

    click.echo("")
    click.echo("Hooks unregistered. Q&A data in ~/.local/share/claude-knowledge-graph/ is preserved.")
    click.echo("Delete it manually if you want to remove all data.")


if __name__ == "__main__":
    main()
