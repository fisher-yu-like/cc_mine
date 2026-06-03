"""
Unified logging setup for cc_mine.
- Console: ANSI-colored output preserved via raw formatter
- File: plain text log at WORKDIR/.cc_mine/logs/agent.log
- Level: controlled by CC_MINE_LOG_LEVEL env var (default INFO)
"""

import logging
import sys
from pathlib import Path

_logger = None
_file_handler = None


def setup_logging(workdir: Path, level: str | None = None) -> logging.Logger:
    """Initialize logging. Call once at startup. Idempotent."""
    global _logger, _file_handler

    if _logger is not None:
        return _logger

    import os
    level = level or os.getenv("CC_MINE_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, level, logging.INFO)

    _logger = logging.getLogger("cc_mine")
    _logger.setLevel(log_level)
    _logger.propagate = False  # don't leak to root logger

    # Console handler — raw output, preserves ANSI escape codes
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(console)

    # File handler — plain text, no ANSI codes
    log_dir = workdir / ".cc_mine" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.FileHandler(
        log_dir / "agent.log", encoding="utf-8", mode="a"
    )
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                          datefmt="%Y-%m-%d %H:%M:%S")
    )
    _logger.addHandler(_file_handler)

    return _logger


def get_logger() -> logging.Logger:
    """Get the module-level logger. Falls back to setup if not initialized."""
    global _logger
    if _logger is None:
        from config import WORKDIR
        setup_logging(WORKDIR)
    return _logger


# Pre-defined ANSI-prefixed convenience methods
def info(msg: str):
    get_logger().info(msg)

def debug(msg: str):
    get_logger().debug(msg)

def warning(msg: str):
    get_logger().warning(msg)

def error(msg: str):
    get_logger().error(msg)
