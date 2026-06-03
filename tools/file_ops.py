from pathlib import Path

from config import WORKDIR
import subprocess


def _read_file_safe(path: Path) -> str:
    """Read file with encoding fallback: utf-8 → gbk → latin-1 (never fails)."""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def safe_path(p: str, cwd: Path = None) -> Path:
    # File tools stay inside the workspace or teammate worktree. Bash remains
    # powerful on purpose and is controlled by the permission hook instead.
    base = cwd or WORKDIR
    path = (base / p).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes workspace: {p}")
    return path




def run_read(path: str, limit: int | None = None,
             offset: int = 0, cwd: Path = None) -> str:
    try:
        lines = _read_file_safe(safe_path(path, cwd)).splitlines()
        offset = max(int(offset or 0), 0)
        limit = int(limit) if limit is not None else None
        lines = lines[offset:]
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str, cwd: Path = None) -> str:
    try:
        fp = safe_path(path, cwd)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


import difflib

MAX_DIFF_OUTPUT = 3000   # Never return more than this
MAX_CONTEXT_LINES = 20   # Max lines in the context snippet


def _render_diff(old_text: str, new_text: str, path: str) -> str:
    """Generate a colored unified diff. +green, -red, @@cyan. Never raw text."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f'a/{path}', tofile=f'b/{path}',
        n=3  # 3 lines of context
    ))
    if not diff:
        return "(no visible difference)"

    colored = []
    for line in diff:
        stripped = line.rstrip()
        if line.startswith('+'):
            colored.append(f"\033[32m{stripped}\033[0m")   # green
        elif line.startswith('-'):
            colored.append(f"\033[31m{stripped}\033[0m")   # red
        elif line.startswith('@@'):
            colored.append(f"\033[36m{stripped}\033[0m")   # cyan
        else:
            colored.append(stripped)

    return '\n'.join(colored)


def _build_snippet(text: str, old_text: str, new_text: str,
                   context_lines: int = 3) -> str:
    """Build a compact snippet showing the change area with line numbers.

    Only includes the changed lines + context_lines of surrounding code.
    Returns a string with at most MAX_CONTEXT_LINES lines.
    """
    idx = text.index(old_text)
    before_text = text[:idx]
    after_text = text[idx + len(old_text):]

    before_all = before_text.split('\n')
    old_all = old_text.split('\n')
    new_all = new_text.split('\n')
    after_all = after_text.split('\n')

    # Trim leading context
    ctx_before = min(len(before_all), context_lines)
    ctx_after = min(len(after_all), context_lines)

    snippet_lines = []
    start_ln = max(0, len(before_all) - ctx_before) + 1

    # Before context
    for i, ln in enumerate(before_all[-ctx_before:]):
        snippet_lines.append(f"  {start_ln + i:4d}  {ln}")

    # Old lines (removed, in red)
    for i, ln in enumerate(old_all):
        snippet_lines.append(f"\033[31m- {start_ln + ctx_before + i:4d}  {ln}\033[0m")

    # New lines (added, in green)
    for i, ln in enumerate(new_all):
        snippet_lines.append(f"\033[32m+ {start_ln + ctx_before + i:4d}  {ln}\033[0m")

    # After context
    after_start = start_ln + ctx_before + len(old_all)
    for i, ln in enumerate(after_all[:ctx_after]):
        snippet_lines.append(f"  {after_start + i:4d}  {ln}")

    # Cap at MAX_CONTEXT_LINES
    if len(snippet_lines) > MAX_CONTEXT_LINES:
        half = MAX_CONTEXT_LINES // 2
        snippet_lines = snippet_lines[:half] + [f"  ... ({len(snippet_lines) - MAX_CONTEXT_LINES} lines omitted) ..."] + snippet_lines[-half:]

    return '\n'.join(snippet_lines)


def run_edit(path: str, old_text: str, new_text: str,
             cwd: Path = None) -> str:
    """Edit a file by replacing old_text with new_text.

    Returns a colored diff of the change — NEVER the full file content.
    Output is capped at MAX_DIFF_OUTPUT characters.
    """
    try:
        fp = safe_path(path, cwd)
        text = _read_file_safe(fp)

        if old_text not in text:
            return f"Error: text not found in {path}"

        # Build diff and snippet BEFORE editing
        diff_text = _render_diff(old_text, new_text, path)
        snippet = _build_snippet(text, old_text, new_text)

        # Apply the edit
        new_content = text.replace(old_text, new_text, 1)
        fp.write_text(new_content, encoding="utf-8")

        # Assemble result — diff first, then snippet context
        separator = "─" * 60
        result = (
            f"Edited {path}\n"
            f"{separator}\n"
            f"{diff_text}\n"
            f"{separator}\n"
            f"Context:\n{snippet}"
        )

        # Hard cap: never return more than MAX_DIFF_OUTPUT chars
        if len(result) > MAX_DIFF_OUTPUT:
            result = result[:MAX_DIFF_OUTPUT] + (
                f"\n... (diff truncated at {MAX_DIFF_OUTPUT} chars. "
                f"Full change applied to {path})"
            )

        return result
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str = "", path: str = "", cwd: Path = None) -> str:
    """Find files matching a glob pattern. Accepts 'pattern' or 'path' as alias."""
    import glob as g
    try:
        base = cwd or WORKDIR
        pattern = pattern or path
        results = []
        for match in g.glob(pattern, root_dir=base):
            if (base / match).resolve().is_relative_to(base):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"
