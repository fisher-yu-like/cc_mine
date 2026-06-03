#!/usr/bin/env python3
"""
Entry point for the ``cc_mine`` CLI command.

Usage::

    cc_mine [--workdir DIR] [--model NAME] [--resume ID] [--yes] [--new]

Installed automatically by ``pip install -e .`` as a console script.
"""

import sys
from pathlib import Path


def main():
    """Launch the cc_mine interactive agent."""
    # Ensure the project root is on sys.path (needed for editable installs
    # and when invoked as a script without install)
    _project_root = Path(__file__).resolve().parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

    # ── Early setup: read env overrides before main imports ──
    import os

    # Parse known flags early so we can set PRIMARY_MODEL before imports
    # (the full parse happens inside main.main())
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg == "--model" and i + 1 < len(argv):
            os.environ["PRIMARY_MODEL"] = argv[i + 1]

    from main import main as _entry
    _entry(argv if argv else None)


if __name__ == "__main__":
    main()
