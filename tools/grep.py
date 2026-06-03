"""
Grep tool for cc_mine — regex search across files with ripgrep-style output.
Uses Python stdlib `re`; respects WORKDIR boundary via safe_path.
"""

import re
from pathlib import Path
from config import WORKDIR
from tools.file_ops import safe_path


def run_grep(pattern: str,
             path: str = ".",
             glob: str = "*",
             ignore_case: bool = False,
             max_results: int = 100,
             context: int = 0) -> str:
    """Search files for a regex pattern. Returns matching lines with file:line numbering.

    Args:
        pattern:   Regex pattern to search for
        path:      File or directory to search (relative to workspace)
        glob:      Glob pattern to filter files (e.g. "*.py", "*.{ts,tsx}")
        ignore_case: Case-insensitive search (-i flag)
        max_results: Limit output to N matches
        context:   Show N lines around each match (-C flag)
    """
    try:
        flags = re.IGNORECASE if ignore_case else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"Error: invalid regex pattern — {e}"

        base = safe_path(path)
        if not base.exists():
            return f"Error: path not found — {path}"

        # Collect files to search
        import fnmatch
        if base.is_file():
            files = [base]
        else:
            files = []
            for p in base.rglob(glob):
                if p.is_file():
                    # Skip binary-looking files
                    if p.suffix in ('.pyc', '.pyo', '.exe', '.dll', '.so', '.png', '.jpg', '.pdf', '.zip'):
                        continue
                    if p.stat().st_size > 1_000_000:  # skip >1MB files
                        continue
                    files.append(p)

        if not files:
            return f"(no files matched glob '{glob}' in {path})"

        # Search
        results = []
        for fp in sorted(files)[:500]:  # max 500 files
            try:
                text = fp.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    text = fp.read_text(encoding="gbk", errors="replace")
                except Exception:
                    continue
            except Exception:
                continue

            lines = text.splitlines()
            matched_lines = set()

            for i, line in enumerate(lines):
                if regex.search(line):
                    top = max(0, i - context)
                    bot = min(len(lines), i + context + 1)

                    # Merge overlapping context windows
                    for j in range(top, bot):
                        if j not in matched_lines:
                            matched_lines.add(j)

            if matched_lines:
                file_results = []
                # Group consecutive line numbers
                sorted_lines = sorted(matched_lines)
                prev = -2
                group = []
                for ln in sorted_lines:
                    if ln > prev + 1 and group:
                        _append_group(file_results, fp, group, lines, regex, len(results) + len(file_results) >= max_results)
                        group = []
                    group.append(ln)
                    prev = ln
                if group:
                    _append_group(file_results, fp, group, lines, regex, len(results) + len(file_results) >= max_results)

                results.extend(file_results)
                if len(results) >= max_results:
                    break

        if not results:
            return f"(no matches for '{pattern}' in {len(files)} file(s))"

        out = "\n".join(results[:max_results])
        if len(results) > max_results:
            out += f"\n... ({len(results) - max_results} more matches truncated)"
        return out

    except ValueError as e:
        return f"Error: path escapes workspace — {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def _append_group(results: list, fp: Path, group: list[int], lines: list[str],
                  regex: re.Pattern, truncated: bool):
    """Format a group of consecutive matching lines."""
    if not group:
        return
    rel = fp.relative_to(WORKDIR) if fp.is_relative_to(WORKDIR) else fp
    start, end = group[0] + 1, group[-1] + 1
    if len(group) <= 3:
        for ln in group:
            prefix = f"{rel}:{ln+1}:"
            results.append(f"{prefix} {lines[ln].rstrip()[:200]}")
    else:
        # Range format: file:line-start:line-end (or show each line)
        for ln in group:
            prefix = f"{rel}:{ln+1}:"
            results.append(f"{prefix}{lines[ln].rstrip()[:200]}")
    if truncated:
        results.append(f"... (truncated)")
