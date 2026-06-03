"""
Spinner state machine for cc_mine.

Shows an animated spinner during LLM API calls and long-running
tool executions, giving users visual feedback that work is happening.

States: IDLE → CALLING_LLM → IDLE
        IDLE → RUNNING_TOOL("bash") → IDLE
        IDLE → THINKING → IDLE

Usage:
    from spinner import start_spinner, set_state, SpinnerState
    start_spinner()
    set_state(SpinnerState.CALLING_LLM)
    # ... do work ...
    set_state(SpinnerState.IDLE)
"""

import sys
import threading
import time
from enum import Enum, auto


class SpinnerState(Enum):
    IDLE = auto()
    CALLING_LLM = auto()
    RUNNING_TOOL = auto()
    THINKING = auto()


# ── Choose frames based on terminal encoding ──
def _can_encode(chars: str) -> bool:
    try:
        chars.encode(sys.stdout.encoding or 'utf-8')
        return True
    except UnicodeEncodeError:
        return False


_FRAMES = (["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
           if _can_encode("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
           else ["|", "/", "-", "\\"])

# ── Internal state ──
_state = SpinnerState.IDLE
_state_lock = threading.Lock()
_active_tool_name = ""
_spinner_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _spinner_loop():
    """Run in background daemon thread; renders spinner frame every 100ms."""
    idx = 0
    while not _stop_event.is_set():
        with _state_lock:
            current = _state
            tool = _active_tool_name

        label = ""
        if current == SpinnerState.CALLING_LLM:
            label = f"  {_FRAMES[idx]} Calling LLM..."
        elif current == SpinnerState.RUNNING_TOOL:
            label = f"  {_FRAMES[idx]} Running {tool}..."
        elif current == SpinnerState.THINKING:
            label = f"  {_FRAMES[idx]} Thinking..."

        if label:
            sys.stdout.write(f"\r\033[K{label}")
            sys.stdout.flush()

        idx = (idx + 1) % len(_FRAMES)
        time.sleep(0.1)

    # Clean up on stop
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


# ── Public API ──


def start_spinner():
    """Start the spinner background thread. Safe to call multiple times."""
    global _spinner_thread, _stop_event
    if _spinner_thread and _spinner_thread.is_alive():
        return
    _stop_event.clear()
    _spinner_thread = threading.Thread(target=_spinner_loop, daemon=True)
    _spinner_thread.start()


def set_state(state: SpinnerState, tool_name: str = ""):
    """Set the spinner state. Thread-safe — callable from any thread.

    Args:
        state: One of SpinnerState.IDLE / CALLING_LLM / RUNNING_TOOL / THINKING.
        tool_name: Human-readable tool name shown during RUNNING_TOOL state.
    """
    global _state, _active_tool_name
    with _state_lock:
        _state = state
        _active_tool_name = tool_name


def stop_spinner():
    """Stop the spinner thread and clear the current line."""
    global _stop_event
    _stop_event.set()


def get_state() -> SpinnerState:
    """Return the current spinner state (for debugging)."""
    with _state_lock:
        return _state
