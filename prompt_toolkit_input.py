"""
prompt_toolkit-based REPL input for cc_mine.

Replaces input()-based multi-line reading with a proper text area:
- Enter = newline, Esc+Enter = submit
- Single-line /commands submit on Enter directly
- Bottom toolbar shows model, mode, plan state, attachment count
- Ctrl+D on empty line exits
"""

import os
import sys
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText, HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.output import create_output as _create_output


# ── Style ──
_INPUT_STYLE = Style.from_dict({
    "prompt": "fg:ansicyan bold",
    "continuation": "fg:#888888",
    "bottom-toolbar": "bg:#2d2d2d fg:#aaaaaa",
    "bottom-toolbar.model": "fg:ansigreen",
    "bottom-toolbar.mode-ask": "fg:ansiyellow bold",
    "bottom-toolbar.plan": "fg:ansiyellow",
    "bottom-toolbar.attachment": "fg:ansimagenta bold",
    "bottom-toolbar.keyhint": "fg:#888888",
})


# ── Condition: detect single-line slash command ──
@Condition
def _is_slash_command() -> bool:
    """True when the buffer is a single-line slash command (starts with /, no newlines)."""
    import sys
    # Safe access to current buffer via prompt_toolkit internals
    try:
        from prompt_toolkit.application.current import get_app
        app = get_app()
        text = app.current_buffer.text
        return text.strip().startswith("/") and "\n" not in text.strip()
    except Exception:
        return False


# ── Key bindings ──
def _create_keybindings() -> KeyBindings:
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _(event):
        """Esc + Enter (or Alt+Enter) submits the buffer."""
        text = event.app.current_buffer.text
        event.app.exit(result=text.strip() if text.strip() else None)

    @kb.add("c-d")
    def _(event):
        """Ctrl+D on empty buffer exits."""
        if event.app.current_buffer.text.strip() == "":
            event.app.exit(result=None)

    @kb.add("enter", filter=_is_slash_command)
    def _(event):
        """Enter submits immediately if single-line slash command."""
        text = event.app.current_buffer.text
        event.app.exit(result=text.strip())

    @kb.add("enter")
    def _(event):
        """Normal Enter inserts newline (multi-line mode)."""
        event.app.current_buffer.insert_text("\n")

    return kb


# ── Bottom toolbar ──
def _make_toolbar() -> Callable[[], FormattedText]:
    """Return a toolbar factory that samples live state on each call."""

    def toolbar() -> FormattedText:
        parts = []

        # Model
        model = os.environ.get("PRIMARY_MODEL", "unknown")
        parts.append(("class:bottom-toolbar", " "))
        parts.append(("class:bottom-toolbar.model", model))
        parts.append(("class:bottom-toolbar", "  "))

        # Mode
        try:
            from mode_manager import get_mode
            mode = get_mode()
        except ImportError:
            mode = "auto"
        mode_class = "bottom-toolbar.mode-ask" if mode == "ask" else "bottom-toolbar"
        parts.append(("class:bottom-toolbar", "| "))
        parts.append((f"class:{mode_class}", mode))
        parts.append(("class:bottom-toolbar", " "))

        # Plan state
        try:
            from planning import get_state as plan_state
            ps = plan_state()
        except ImportError:
            ps = "idle"
        if ps not in ("idle",):
            parts.append(("class:bottom-toolbar", "| "))
            parts.append(("class:bottom-toolbar.plan", f"plan:{ps}"))
            parts.append(("class:bottom-toolbar", " "))

        # Attachment count
        try:
            from multimodal import pending_count
            n = pending_count()
        except ImportError:
            n = 0
        if n > 0:
            parts.append(("class:bottom-toolbar", "| "))
            att_text = f"{n} attachment(s)" if n > 1 else "1 attachment"
            parts.append(("class:bottom-toolbar.attachment", att_text))
            parts.append(("class:bottom-toolbar", " "))

        # Spacer + key hint (right side)
        parts.append(("class:bottom-toolbar", " " * 6))
        parts.append(("class:bottom-toolbar.keyhint",
                      "Esc+Enter: send  |  Ctrl+D: exit  |  /file: attach"))

        return FormattedText(parts)

    return toolbar


# ── Session ──
_SESSION: PromptSession | None = None


def _get_session() -> PromptSession:
    """Lazy-init singleton PromptSession.

    Handles Git Bash / Cygwin on Windows where Win32Output fails because
    there is no real console screen buffer.
    """
    global _SESSION
    if _SESSION is None:
        output = _create_output_safe()

        _SESSION = PromptSession(
            message=HTML("<prompt>cc_mine</prompt> &gt; "),
            multiline=True,
            prompt_continuation=HTML("<continuation>...  </continuation>"),
            key_bindings=_create_keybindings(),
            style=_INPUT_STYLE,
            bottom_toolbar=_make_toolbar(),
            complete_while_typing=False,
            enable_history_search=False,
            mouse_support=False,
            output=output,
        )
    return _SESSION


def _create_output_safe():
    """Create prompt_toolkit output, with fallbacks for Git Bash / Cygwin."""
    # Attempt 1: default detection (works on native Windows console + Unix TTYs)
    try:
        return _create_output()
    except Exception:
        pass

    # Attempt 2: Vt100_Output for Git Bash / Windows Terminal / ConEmu
    try:
        from prompt_toolkit.output.vt100 import Vt100_Output
        from prompt_toolkit.output import ColorDepth
        from prompt_toolkit.data_structures import Size
        import shutil
        term_size = shutil.get_terminal_size((80, 24))
        return Vt100_Output(
            sys.stdout,
            get_size=lambda: Size(term_size.lines, term_size.columns),
            default_color_depth=ColorDepth.DEPTH_24_BIT,
        )
    except Exception:
        pass

    # Attempt 3: DummyOutput — no colors, but won't crash
    from prompt_toolkit.output import DummyOutput
    return DummyOutput()


def read_input() -> str | None:
    """Read multi-line user input via prompt_toolkit.

    Returns:
        The user's text, or None if the user wants to exit (Ctrl+D on empty line).
    """
    try:
        session = _get_session()
        text = session.prompt()
        if text is None:
            return None
        return text.strip() if text.strip() else None
    except KeyboardInterrupt:
        return None
    except EOFError:
        return None


def terminal_print_above(text: str):
    """Print text above the prompt_toolkit input area. Thread-safe.

    Use this instead of bare print() when prompt_toolkit's prompt()
    might be active (e.g., from background cron threads).
    Falls back to plain print() if prompt_toolkit output is unavailable.
    """
    try:
        from prompt_toolkit.shortcuts import print_formatted_text
        from prompt_toolkit.formatted_text import ANSI
        # Override output to avoid Win32Output crash on Git Bash
        print_formatted_text(
            ANSI(text),
            output=_create_output_safe(),
        )
    except Exception:
        print(text)
