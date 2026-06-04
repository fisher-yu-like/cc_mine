"""
Debug attempt tracker for cc_mine.

Tracks consecutive failed fix attempts for debugging tasks. After 3+
failures, automatically triggers web search (domestic + international)
to find solutions, extracts actionable steps, and presents a Fix Plan
to the user for approval before executing.

Usage:
    from debug_tracker import record_failure, should_trigger_web_search
"""
import re
import threading

# Per-session failure tracking
_failure_counts: dict[str, int] = {}
_lock = threading.Lock()
_search_round = 0

DEBUG_KEYWORDS = [
    "debug", "fix", "bug", "error", "修复", "调试",
    "bug", "错误", "报错", "exception", "traceback",
    "not working", "doesn't work", "broken",
]

RETRY_THRESHOLD = 3
MAX_SEARCH_ROUNDS = 2


def is_debug_context(query: str) -> bool:
    """Detect if a query is a debugging / fix request."""
    qlower = query.lower()
    return any(kw in qlower for kw in DEBUG_KEYWORDS)


def record_failure(error_msg: str):
    """Record a failed fix attempt, extracting a stable error key."""
    key = _extract_error_key(error_msg)
    with _lock:
        _failure_counts[key] = _failure_counts.get(key, 0) + 1


def should_trigger_web_search() -> bool:
    """Check if failure count >= threshold."""
    with _lock:
        return (max(_failure_counts.values(), default=0) >= RETRY_THRESHOLD and
                _search_round < MAX_SEARCH_ROUNDS)


def get_failure_count() -> int:
    with _lock:
        return max(_failure_counts.values(), default=0)


def get_search_round() -> int:
    with _lock:
        return _search_round


def increment_search_round():
    global _search_round
    with _lock:
        _search_round += 1


def reset_failures():
    global _search_round
    with _lock:
        _failure_counts.clear()
        _search_round = 0


def build_search_queries() -> list[tuple[str, str]]:
    """Build (query, language) pairs from accumulated error keys."""
    with _lock:
        keys = sorted(_failure_counts.items(), key=lambda x: -x[1])

    queries = []
    for key, _ in keys[:3]:  # top 3 error patterns
        # Strip noise
        clean = key[:80]
        queries.append((f"{clean} solution 2026", "en"))
        queries.append((f"{clean} 解决方法 最新", "zh"))
    return queries


def format_fix_plan(error_context: str, solutions: list[dict]) -> str:
    """Format a Fix Plan for user approval.

    Args:
        error_context: Summary of the error.
        solutions: List of {title, source_url, steps, confidence}.
    """
    lines = [
        "=" * 60,
        "  [Fix Plan] -- Debug Auto-Search Results",
        "=" * 60,
        f"  Error: {error_context[:120]}",
        f"  Failed attempts: {get_failure_count()}",
        f"  Search round: {get_search_round() + 1}/{MAX_SEARCH_ROUNDS}",
        "=" * 60,
        "  Found Solutions:",
        "",
    ]

    for i, sol in enumerate(solutions, 1):
        lines.append(f"  {i}. {sol.get('title', 'Unknown')}")
        lines.append(f"     Source: {sol.get('source_url', 'N/A')}")
        lines.append(f"     Steps: {sol.get('steps', 'See source')}")
        lines.append(f"     Confidence: {sol.get('confidence', 'medium')}")
        lines.append("")

    lines.extend([
        "=" * 60,
        "  Reply:",
        '    "approve" -- execute this fix',
        '    "reject"  -- skip, I will handle it',
        '    "retry"   -- search again with different terms',
        "=" * 60,
    ])

    return "\n".join(lines)


# ── Test command detection ──

TEST_PATTERNS = [
    r'\bpytest\b', r'\bnpm\s+(test|run\s+test)\b', r'\byarn\s+test\b',
    r'\bcargo\s+test\b', r'\bgo\s+test\b', r'\bunittest\b',
    r'\bpython\s+-m\s+(unittest|pytest)\b', r'\bpython\s+\S*test\S*\.py\b',
    r'\bjest\b', r'\bnpx\s+vitest\b', r'\bnpx\s+cypress\b',
    r'\bmake\s+test\b', r'\bctest\b', r'\bdotnet\s+test\b',
    r'\bphpunit\b', r'\brspec\b', r'\bmvn\s+test\b', r'\bgradle\s+test\b',
    r'\btox\b', r'\bnosetests\b', r'\bpython\s+setup\.py\s+test\b',
]


def is_test_command(command: str) -> bool:
    """Detect if a bash command is running a test suite."""
    cmd_lower = command.lower()
    return any(re.search(p, cmd_lower) for p in TEST_PATTERNS)


def extract_error_lines(output: str, max_lines: int = 8) -> str:
    """Extract error-relevant lines from test output.

    Captures: FAILED, Error, Traceback, AssertionError, E-prefix pytest lines,
    and summary lines like '=== X failed in Y.YYs ==='.
    """
    lines = output.split('\n')
    error_lines = []
    in_traceback = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_traceback:
                error_lines.append('')
            continue

        # Traceback block
        if 'Traceback (most recent call last)' in stripped:
            in_traceback = True
            error_lines.append(stripped)
            continue

        if in_traceback:
            if (stripped.startswith('File ') or
                any(kw in stripped for kw in ['Error:', 'Error ', 'Exception', 'assert ']) or
                    (stripped and stripped[0].isspace())):
                error_lines.append(stripped)
                if any(kw in stripped for kw in ['Error:', 'Error ']):
                    in_traceback = False  # end of this traceback
                continue
            in_traceback = False

        # Error indicators
        if any(kw in stripped for kw in [
            'FAILED', 'FAIL:', 'ERROR:', 'ERRORS:',
            'AssertionError', 'assert ', 'E   ',
            'short test summary', '===']):
            error_lines.append(stripped)

    if not error_lines:
        # Fallback: last 20 lines of output
        error_lines = lines[-20:]

    return '\n'.join(error_lines[:max_lines * 3])[:2000]


def _extract_error_key(msg: str) -> str:
    """Extract a stable error key, stripping line numbers and timestamps."""
    key = re.sub(r'File ".*?", line \d+', '', str(msg))
    key = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', '', key)
    key = re.sub(r'0x[0-9a-fA-F]+', '', key)
    key = re.sub(r'/[^ ]*\.py:\d+', '', key)
    return key.strip()[:80]
