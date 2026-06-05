"""
Post-processing renderer for bash and git tool results.

Formats output with command headers, status indicators, and truncation.
"""

import re


def render_bash_result(command: str, output: str, exit_code: int = 0) -> str:
    """Format a bash command result for display.

    Returns a markdown-style fenced code block with:
    - Command with $ prefix
    - Status indicator [OK] or [EXIT:N]
    - Cleaned output (ANSI stripped, truncated if huge)
    """
    # Strip raw ANSI escape sequences
    clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)

    # Truncate extremely large outputs (collapse handled by render_tool_output)
    if len(clean) > 100000:
        lines = clean.count('\n') + 1
        chars = len(clean)
        clean = f"[{lines} lines, {chars} chars]\n{clean[:20000]}\n... (output truncated)"

    status = "[OK]" if exit_code == 0 else f"[EXIT:{exit_code}]"
    return f"```bash\n$ {command}\n{status}\n{clean}\n```"


def render_git_result(args: list[str], output: str, success: bool) -> str:
    """Format a git command result for display.

    Returns a markdown-style fenced code block with:
    - Full git command
    - [OK] or [FAILED] status
    - Output (truncated at 5000 chars)
    """
    status = "[OK]" if success else "[FAILED]"
    out = output[:5000] if output else "(no output)"

    return f"```\n$ git {' '.join(args)}\n{status}\n{out}\n```"


def render_diff_output(diff_text: str) -> str:
    """Format a git diff for display (truncated at 10000 chars)."""
    return f"```diff\n{diff_text[:10000]}\n```"
