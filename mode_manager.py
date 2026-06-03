"""
Mode manager for cc_mine.

Controls agent autonomy level:
  'auto' — proceed without asking (default)
  'ask'  — confirm before file writes, bash, git, MCP tools

Usage:
    from mode_manager import get_mode, set_mode
"""
import os
import threading

_modes = ["auto", "ask"]
_current_mode = "auto"
_mode_lock = threading.Lock()

# Tools that trigger confirmation in 'ask' mode
ASK_TOOLS = {"bash", "write_file", "edit_file", "git"}


def get_mode() -> str:
    with _mode_lock:
        return _current_mode


def set_mode(mode: str) -> str:
    global _current_mode
    mode = mode.strip().lower()
    if mode not in _modes:
        return f"Invalid mode: {mode}. Use 'auto' or 'ask'."
    with _mode_lock:
        _current_mode = mode
    return f"Mode set to '{mode}'."


def toggle_mode() -> str:
    global _current_mode
    with _mode_lock:
        _current_mode = "ask" if _current_mode == "auto" else "auto"
        return f"Mode toggled to '{_current_mode}'."


def init_mode():
    """Read default mode from env var CC_MINE_MODE."""
    default = os.environ.get("CC_MINE_MODE", "auto").strip().lower()
    if default in _modes:
        global _current_mode
        _current_mode = default
