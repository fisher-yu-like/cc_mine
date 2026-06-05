from pathlib import Path
import os as _os
import subprocess
import time
from config import WORKDIR, TOOL_RESULTS_DIR
from tools.result_renderer import render_bash_result

# ── Expose last exit code for test-failure detection ──
_last_exit_code: int = 0


def get_last_exit_code() -> int:
    """Return the exit code of the most recent bash command."""
    return _last_exit_code


def run_bash(command: str, cwd: Path = None,
             run_in_background: bool = False) -> str:
    global _last_exit_code
    try:
        # Force color tools (pytest, npm, etc.) to emit ANSI colors even when
        # stdout is a pipe (subprocess.PIPE). The raw colored output is saved
        # to disk; render_bash_result strips ANSI for the LLM message.
        env = {**_os.environ, "FORCE_COLOR": "1", "TERM": "xterm-256color"}
        r = subprocess.run(command, shell=True, cwd=cwd or WORKDIR,
                           capture_output=True, timeout=120, env=env)
        _last_exit_code = r.returncode
        out = (r.stdout or b"") + (r.stderr or b"")
        text = out.decode("utf-8", errors="replace").strip()
        raw = text[:50000] if text else "(no output)"

        # Save full output (with ANSI colors intact) for user inspection
        output_id = f"bash_{int(time.time() * 1000)}"
        output_path = TOOL_RESULTS_DIR / f"{output_id}.txt"
        if not TOOL_RESULTS_DIR.exists():
            TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path.write_text(raw, encoding="utf-8", errors="replace")

        # Return full output — collapse/truncation is handled by render_tool_output
        rendered = render_bash_result(command, raw, r.returncode)
        total_lines = raw.count('\n') + 1
        if len(raw) > 50000 or total_lines > 500:
            rendered += f"\n\033[90m[file: {output_path}]\033[0m"
        return rendered
    except subprocess.TimeoutExpired:
        _last_exit_code = -1
        return "Error: Timeout (120s)"
