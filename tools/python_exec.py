"""
Layer 3: Python code execution tool.

Instead of N LLM roundtrips (glob → read → process → write),
the agent writes a Python script and executes it in ONE call.

Usage:
    run_python('''
    import glob, json
    for f in glob.glob("*.json"):
        ...
    ''')
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from config import WORKDIR, TOOL_RESULTS_DIR


def run_python(script: str, timeout: int = 30) -> str:
    """Execute a Python script and return stdout, stderr, and exit code.

    The script runs in the project WORKDIR with PYTHONUNBUFFERED=1.
    Output is capped at 10000 chars; full output saved to .task_outputs/.

    Args:
        script: Python source code to execute.
        timeout: Max seconds before killing the process (default 30).
    """
    # Write to temp file for proper tracebacks
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".py", dir=WORKDIR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)

        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}

        r = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            timeout=timeout,
            cwd=str(WORKDIR),
            env=env,
        )

        out = (r.stdout or b"") + (r.stderr or b"")
        text = out.decode("utf-8", errors="replace").strip()
        if not text:
            text = "(no output)"

        # Save full output
        if not TOOL_RESULTS_DIR.exists():
            TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = TOOL_RESULTS_DIR / f"python_{int(time.time() * 1000)}.txt"
        output_path.write_text(text, encoding="utf-8", errors="replace")

        # Return preview
        preview = text[:10000]
        status = "[OK]" if r.returncode == 0 else f"[EXIT:{r.returncode}]"
        result = f"```python\n{status}\n{preview}\n```"
        if len(text) > 10000:
            result += f"\n\033[90m[file: {output_path}]\033[0m"
        return result

    except subprocess.TimeoutExpired:
        return f"Error: Python script timed out after {timeout}s"

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
