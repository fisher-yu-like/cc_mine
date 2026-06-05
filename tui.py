"""
Full-screen TUI for cc_mine using Rich Live + Layout.

Layout:
    header (1 line): status bar - model, mode, plan state
    body (flexible): scrollable agent output - tool calls + results
    footer (1 line): status text (Running... / Press Ctrl+C to stop)

Usage:
    tui = AgentTUI()
    tui.start()
    tui.render_tool("bash", "Running pytest...", "all tests passed")
    tui.set_status("Running... Press Ctrl+C to stop")
    tui.stop()
"""

import os
from threading import Lock

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel


# ── Module-level active TUI tracking ──
_active_tui: "AgentTUI | None" = None


def get_active() -> "AgentTUI | None":
    """Return the currently active AgentTUI instance, or None."""
    return _active_tui


def _build_layout() -> Layout:
    """Create the 3-section TUI layout."""
    layout = Layout()
    layout.split(
        Layout(name="header", size=1),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=1),
    )
    layout["header"].update(Panel("", border_style="dim"))
    layout["body"].update(Panel("", border_style="dim"))
    layout["footer"].update(Panel("", border_style="dim"))
    return layout


class AgentTUI:
    """Manages the Rich Live full-screen TUI display."""

    def __init__(self, console: Console | None = None):
        self._console = console or Console()
        self._layout = _build_layout()
        self._live: Live | None = None
        self._outputs: list[Panel] = []
        self._lock = Lock()
        self._started = False

    # ── Lifecycle ──

    def start(self):
        """Enter full-screen TUI mode."""
        if self._started:
            return
        global _active_tui
        _active_tui = self
        self._update_header()
        self._update_footer("Ctrl+C: stop  |  Running...")
        self._live = Live(
            self._layout,
            console=self._console,
            screen=True,
            refresh_per_second=8,
            transient=False,
        )
        self._live.start()
        self._started = True

    def stop(self):
        """Exit full-screen TUI mode. Restores the terminal."""
        global _active_tui
        _active_tui = None
        if not self._started or self._live is None:
            return
        try:
            self._live.stop()
        except Exception:
            pass
        self._started = False
        self._live = None

    # ── Content updates ──

    def set_status(self, text: str):
        """Update the footer status text."""
        self._update_footer(text)
        self._refresh()

    def render_tool(self, tool_name: str, description: str, result: str = ""):
        """Add a tool execution entry to the body."""
        with self._lock:
            lines = [f"[cyan][{tool_name}][/cyan] {description}"]
            if result:
                preview = result[:600]
                if len(result) > 600:
                    preview += f"\n[dim]... ({len(result) - 600} more chars)[/dim]"
                lines.append("[dim]" + "─" * 40 + "[/dim]")
                lines.append(preview)

            panel = Panel("\n".join(lines), border_style="dim", padding=(0, 1))
            self._outputs.append(panel)
            if len(self._outputs) > 100:
                self._outputs = self._outputs[-60:]
            self._render_body()
            self._refresh()

    def render_text(self, text: str):
        """Add a plain text line to the body."""
        with self._lock:
            self._outputs.append(Panel(text, border_style="dim", padding=(0, 1)))
            if len(self._outputs) > 100:
                self._outputs = self._outputs[-60:]
            self._render_body()
            self._refresh()

    # ── Internal ──

    def _update_header(self):
        """Refresh the header status bar."""
        model = os.environ.get("PRIMARY_MODEL", "unknown")
        try:
            from mode_manager import get_mode
            mode = get_mode()
        except ImportError:
            mode = "auto"
        mode_color = "green" if mode == "auto" else "yellow"
        try:
            from planning import get_state as plan_state
            ps = plan_state()
        except ImportError:
            ps = "idle"
        plan_str = f" | [yellow]plan: {ps}[/yellow]" if ps not in ("idle",) else ""
        header_text = (
            f"[bold cyan]cc_mine[/bold cyan]"
            f" [dim]model:[/dim] [green]{model}[/green]"
            f" [dim]mode:[/dim] [{mode_color}]{mode}[/{mode_color}]"
            f"{plan_str}"
        )
        self._layout["header"].update(Panel(header_text, border_style="dim"))

    def _update_footer(self, text: str):
        """Update the footer bar."""
        self._layout["footer"].update(
            Panel(f"[dim]{text}[/dim]", border_style="dim")
        )

    def _render_body(self):
        """Render accumulated outputs into the body layout."""
        avail = max(self._console.height - 3, 5)
        visible = self._outputs[-max(avail // 5, 3):]
        if visible:
            self._layout["body"].update(Group(*visible))
        else:
            self._layout["body"].update(
                Panel("[dim]Waiting for agent...[/dim]", border_style="dim")
            )

    def _refresh(self):
        """Trigger a Live refresh if active."""
        if self._live and self._started:
            try:
                self._live.refresh()
            except Exception:
                pass
