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


def render_separator():
    """Render a horizontal rule between response area and input."""
    from terminal_renderer import render_separator as _rs
    _rs()
