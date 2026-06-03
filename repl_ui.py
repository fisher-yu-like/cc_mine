"""
REPL interface rendering for cc_mine.

Provides the structured layout: status header + agent response body + input prompt.
Uses the terminal_renderer for all visual output.
"""

import os
from terminal_renderer import get_console

_console = get_console()


def _get_mode() -> str:
    """Return current agent mode. Uses mode_manager if available (Step 13)."""
    try:
        from mode_manager import get_mode
        return get_mode()
    except ImportError:
        return "auto"


def render_header():
    """Render the status header bar before the input prompt.

    Shows: cc_mine | model | mode | plan state
    """
    model = os.environ.get("PRIMARY_MODEL", "unknown")

    from planning import get_state as plan_state
    ps = plan_state()

    parts = [
        "[bold cyan]cc_mine[/bold cyan]",
        f"[dim]model:[/dim] [green]{model}[/green]",
    ]

    mode = _get_mode()
    mode_color = "green" if mode == "auto" else "yellow"
    parts.append(f"[dim]mode:[/dim] [{mode_color}]{mode}[/{mode_color}]")

    if ps and ps != "idle":
        parts.append(f"[yellow]plan: {ps}[/yellow]")

    separator = " [dim]|[/dim] "
    _console.print(separator.join(parts))


def render_input_prompt(first_line: bool, attachment_count: int = 0) -> str:
    """Build the styled REPL input prompt string.

    Args:
        first_line: True for the first line of multi-line input.
        attachment_count: Number of pending attachments.

    Returns:
        A Rich-markup string ready for input().
    """
    from planning import get_state as plan_state
    ps = plan_state()

    parts = []
    if ps == "planning":
        parts.append("[yellow][plan][/yellow]")
    elif ps == "plan_ready":
        parts.append("[yellow][awaiting][/yellow]")
    elif ps == "plan_approved":
        parts.append("[green][exec][/green]")

    if attachment_count > 0:
        parts.append(f"[magenta][{attachment_count}att][/magenta]")

    mode = _get_mode()
    if mode == "ask":
        parts.append("[yellow][ask][/yellow]")

    prefix = " ".join(parts) + " " if parts else ""

    if first_line:
        return f"{prefix}[cyan]s01 >> [/cyan]"
    else:
        return f"{prefix}[cyan]...    [/cyan]"


def render_separator():
    """Render a horizontal rule between response area and input."""
    from terminal_renderer import render_separator as _rs
    _rs()
