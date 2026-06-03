"""
Terminal renderer for cc_mine.

Wraps the Rich library for consistent, styled terminal output.
All visual output should pass through this module — no bare print()
with raw ANSI escape codes elsewhere.

Usage:
    from terminal_renderer import render_assistant, render_tool_call, ...
"""

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.markdown import Markdown
from rich.text import Text

_console = Console(highlight=False)  # We control highlighting ourselves

# ═══════════════════════════════════════════════════════════════
# Core rendering functions
# ═══════════════════════════════════════════════════════════════


def render_assistant(text: str):
    """Render the assistant's markdown response."""
    if not text:
        return
    _console.print(Markdown(text))


def render_tool_call(tool_name: str, description: str):
    """Render a tool call notification as a compact one-liner."""
    _console.print(f"  [cyan][{tool_name}][/cyan] {description}")


def render_tool_result(tool_name: str, output: str, max_lines: int = 30):
    """Render a tool result. Large outputs are truncated automatically."""
    lines = output.split('\n')
    if len(lines) > max_lines:
        truncated = '\n'.join(lines[:max_lines]) + \
                     f"\n[dim]... ({len(lines) - max_lines} more lines)[/dim]"
        _console.print(Panel(truncated, title=f"[dim]{tool_name} result[/dim]",
                             border_style="dim", title_align="left"))
    else:
        _console.print(Panel(output, title=f"[dim]{tool_name} result[/dim]",
                             border_style="dim", title_align="left"))


def render_info(message: str):
    """Render an informational / debug message."""
    _console.print(f"[dim]{message}[/dim]")


def render_warning(message: str):
    """Render a warning."""
    _console.print(f"[yellow]{message}[/yellow]")


def render_error(message: str):
    """Render an error."""
    _console.print(f"[bold red]{message}[/bold red]")


# ═══════════════════════════════════════════════════════════════
# Tool execution rendering (Step 7: Panel cards + Table summary)
# ═══════════════════════════════════════════════════════════════


def render_tool_execution(tool_name: str, description: str,
                          status: str = "running"):
    """Render a tool execution as a one-liner with icon.

    Args:
        tool_name: e.g. 'bash', 'read_file'.
        description: Human-readable description from hooks._describe_tool.
        status: 'running' (cyan >), 'done' (green OK), 'blocked' (red X),
                'error' (red !).
    """
    icon = {"running": "[cyan]>[/cyan]",
            "done": "[green]OK[/green]",
            "blocked": "[red]X[/red]",
            "error": "[red]![/red]"}.get(status, "?")

    _console.print(f"  {icon} [cyan][{tool_name}][/cyan] {description}")


def render_tool_output(tool_name: str, output: str, max_lines: int = 20):
    """Render tool output with auto-detection of content type.

    - bash/git: syntax-highlighted code block
    - read_file: dim panel with file content
    - generic: dim panel, truncated if large
    """
    lines = output.split('\n')

    if tool_name in ("bash", "git"):
        code = '\n'.join(lines[:max_lines])
        if len(lines) > max_lines:
            code += f"\n[dim]... ({len(lines) - max_lines} more lines)[/dim]"
        _console.print(Syntax(code, "bash", theme="monokai",
                              line_numbers=False, background_color="default"))
    elif tool_name in ("read_file", "write_file", "edit_file"):
        truncated = '\n'.join(lines[:max_lines])
        if len(lines) > max_lines:
            truncated += f"\n[dim]... ({len(lines) - max_lines} more lines)[/dim]"
        _console.print(Panel(truncated,
                             title=f"[dim]{tool_name} output[/dim]",
                             border_style="dim"))
    else:
        if len(lines) > max_lines:
            output = '\n'.join(lines[:max_lines]) + \
                     f"\n[dim]... ({len(lines) - max_lines} more lines)[/dim]"
        _console.print(Panel(output,
                             title=f"[dim]{tool_name} output[/dim]",
                             border_style="dim"))


def render_turn_summary(tool_results: list[dict]):
    """After a turn, render a compact Table summary of all tool calls.

    Args:
        tool_results: List of dicts with keys: name, status, preview.
    """
    if not tool_results:
        return
    table = Table(title="Turn Summary", show_header=True,
                  header_style="bold", title_style="dim")
    table.add_column("Tool", style="cyan", width=16)
    table.add_column("Status", style="green", width=8)
    table.add_column("Result", style="dim", max_width=60)
    for r in tool_results:
        table.add_row(r.get("name", "?"),
                      r.get("status", "done"),
                      str(r.get("preview", ""))[:60])
    _console.print(table)


def render_success(message: str):
    """Render a success message."""
    _console.print(f"[green]{message}[/green]")


def render_separator():
    """Render a horizontal separator line."""
    _console.print("[dim]" + "─" * min(_console.width, 120) + "[/dim]")


# ═══════════════════════════════════════════════════════════════
# Specialized rendering
# ═══════════════════════════════════════════════════════════════


def render_code_block(code: str, language: str = "python",
                      max_lines: int = 30):
    """Render a syntax-highlighted code block."""
    lines = code.split('\n')
    if len(lines) > max_lines:
        code = '\n'.join(lines[:max_lines]) + \
               f"\n# ... ({len(lines) - max_lines} more lines)"
    _console.print(Syntax(code, language, theme="monokai",
                          line_numbers=True,
                          background_color="default"))


def render_diff(diff_text: str, path: str = ""):
    """Render a unified diff with syntax highlighting."""
    title = f"[cyan]diff: {path}[/cyan]" if path else "[cyan]diff[/cyan]"
    _console.print(Panel(
        Syntax(diff_text, "diff", theme="monokai",
               line_numbers=False, background_color="default"),
        title=title, border_style="cyan", title_align="left"
    ))


def render_table(headers: list[str], rows: list[list[str]],
                 title: str = ""):
    """Render a Rich Table."""
    table = Table(title=title, show_header=True, header_style="bold")
    for h in headers:
        table.add_column(h)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    _console.print(table)


def render_panel(content: str, title: str = "", style: str = "dim"):
    """Render content inside a bordered Panel."""
    _console.print(Panel(content, title=title, border_style=style,
                         title_align="left"))


# ═══════════════════════════════════════════════════════════════
# Prompt rendering
# ═══════════════════════════════════════════════════════════════


def render_prompt(text: str):
    """Render the REPL prompt inline (no newline)."""
    _console.print(text, end="")


# ═══════════════════════════════════════════════════════════════
# Layout (used by REPL UI in Step 14)
# ═══════════════════════════════════════════════════════════════

from rich.layout import Layout


def create_layout() -> Layout:
    """Create the default 3-section REPL layout."""
    layout = Layout()
    layout.split(
        Layout(name="header", size=1),
        Layout(name="body"),
        Layout(name="input", size=1),
    )
    return layout


# ═══════════════════════════════════════════════════════════════
# Compatibility: raw Console access for advanced use
# ═══════════════════════════════════════════════════════════════

def get_console() -> Console:
    """Return the shared Console instance."""
    return _console
