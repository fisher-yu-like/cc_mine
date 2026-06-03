"""
Concurrent query queue for cc_mine.

Allows users to submit new queries while the agent is busy processing.
Queued queries are processed in FIFO order after the current turn completes.

Usage:
    from query_queue import mark_busy, enqueue_query, dequeue_query
"""
import queue
import threading

_pending_queries: queue.Queue[str] = queue.Queue()
_is_busy = threading.Event()


def mark_busy():
    _is_busy.set()


def mark_idle():
    _is_busy.clear()


def is_busy() -> bool:
    return _is_busy.is_set()


def enqueue_query(query: str):
    """Add a query to the pending queue."""
    _pending_queries.put(query)


def dequeue_query() -> str | None:
    """Get next pending query, or None if queue is empty."""
    try:
        return _pending_queries.get_nowait()
    except queue.Empty:
        return None


def pending_count() -> int:
    return _pending_queries.qsize()
