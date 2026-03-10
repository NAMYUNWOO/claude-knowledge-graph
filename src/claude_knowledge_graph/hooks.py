"""Manage Claude Code hooks registration for claude-knowledge-graph.

Reads/writes ~/.claude/settings.json to add or remove ckg hooks
while preserving existing user hooks.
"""

import json
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Marker to identify hooks managed by ckg
CKG_MARKER = "claude-knowledge-graph"

HOOK_COMMAND = "python3 -m claude_knowledge_graph.qa_logger"

HOOKS_CONFIG = {
    "UserPromptSubmit": {
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": HOOK_COMMAND,
                "description": f"[{CKG_MARKER}] Capture user prompts",
            }
        ],
    },
    "Stop": {
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": HOOK_COMMAND,
                "description": f"[{CKG_MARKER}] Capture Q&A pairs",
            }
        ],
    },
}


def _load_settings() -> dict:
    """Load Claude Code settings, creating file if needed."""
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_settings(settings: dict) -> None:
    """Save Claude Code settings."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")


def _is_ckg_matcher_group(group: dict) -> bool:
    """Check if a matcher group belongs to ckg.

    Also detects legacy flat-format hooks ({type, command, description})
    so they can be cleaned up during unregister.
    """
    # New format: matcher group with hooks array
    for hook in group.get("hooks", []):
        desc = hook.get("description", "")
        cmd = hook.get("command", "")
        if CKG_MARKER in desc or "claude_knowledge_graph" in cmd:
            return True
    # Legacy flat format: {type, command, description} without hooks array
    if "hooks" not in group:
        desc = group.get("description", "")
        cmd = group.get("command", "")
        if CKG_MARKER in desc or "claude_knowledge_graph" in cmd:
            return True
    return False


def register_hooks() -> bool:
    """Register ckg hooks in Claude Code settings.

    Returns True if hooks were added, False if already present.
    """
    settings = _load_settings()
    hooks = settings.setdefault("hooks", {})
    changed = False

    for event_name, hook_config in HOOKS_CONFIG.items():
        event_hooks = hooks.setdefault(event_name, [])

        # Check if ckg hook already exists for this event
        already_registered = any(_is_ckg_matcher_group(h) for h in event_hooks)
        if not already_registered:
            event_hooks.append(hook_config)
            changed = True

    if changed:
        _save_settings(settings)

    return changed


def unregister_hooks() -> bool:
    """Remove ckg hooks from Claude Code settings.

    Returns True if hooks were removed, False if none found.
    """
    settings = _load_settings()
    hooks = settings.get("hooks", {})
    changed = False

    for event_name in list(hooks.keys()):
        original = hooks[event_name]
        filtered = [h for h in original if not _is_ckg_matcher_group(h)]
        if len(filtered) != len(original):
            hooks[event_name] = filtered
            changed = True
        # Clean up empty arrays
        if not hooks[event_name]:
            del hooks[event_name]

    if changed:
        if not hooks:
            settings.pop("hooks", None)
        _save_settings(settings)

    return changed


def check_hooks() -> dict[str, bool]:
    """Check which ckg hooks are currently registered.

    Returns dict mapping event name to registration status.
    """
    settings = _load_settings()
    hooks = settings.get("hooks", {})

    status = {}
    for event_name in HOOKS_CONFIG:
        event_hooks = hooks.get(event_name, [])
        status[event_name] = any(_is_ckg_matcher_group(h) for h in event_hooks)

    return status
