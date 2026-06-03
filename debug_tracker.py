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


def _extract_error_key(msg: str) -> str:
    """Extract a stable error key, stripping line numbers and timestamps."""
    key = re.sub(r'File ".*?", line \d+', '', str(msg))
    key = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', '', key)
    key = re.sub(r'0x[0-9a-fA-F]+', '', key)
    key = re.sub(r'/[^ ]*\.py:\d+', '', key)
    return key.strip()[:80]
