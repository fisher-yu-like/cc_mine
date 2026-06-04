"""
Session-level tool output store + collapse preference.

Used by main.py to store last-turn tool outputs for /expand /collapse commands,
and by terminal_renderer.py to decide collapsed vs expanded rendering.

Follows the same module-level state pattern as mode_manager.py.
"""

import threading

# ── Output store ──
_last_outputs: list[dict] = []
_outputs_lock = threading.Lock()

# ── Global collapse default ──
_collapse_default: bool = True


def store_output(name: str, text: str) -> int:
    """Store a tool output. Returns 1-based index for user-facing display."""
    entry = {
        "name": name,
        "output": text[:100000],  # cap at 100KB to avoid memory bloat
        "lines": text.count("\n") + 1 if text else 0,
    }
    with _outputs_lock:
        _last_outputs.append(entry)
        return len(_last_outputs)  # 1-based


def get_output(index: int) -> dict | None:
    """Get output by 1-based index as typed by the user (/expand 1)."""
    with _outputs_lock:
        i = index - 1
        if 0 <= i < len(_last_outputs):
            return _last_outputs[i]
        return None


def clear_outputs():
    """Reset the output store. Called at the start of each user turn."""
    global _last_outputs
    with _outputs_lock:
        _last_outputs = []


def output_count() -> int:
    """Number of stored outputs from the last turn."""
    with _outputs_lock:
        return len(_last_outputs)


def toggle_collapse() -> bool:
    """Flip the global collapse default. Returns new value."""
    global _collapse_default
    _collapse_default = not _collapse_default
    return _collapse_default


def get_collapse_default() -> bool:
    """Get current collapse preference."""
    return _collapse_default
