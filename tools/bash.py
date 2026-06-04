from pathlib import Path
import subprocess
import time
from config import WORKDIR, TOOL_RESULTS_DIR
from tools.result_renderer import render_bash_result


def run_bash(command: str, cwd: Path = None,
             run_in_background: bool = False) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=cwd or WORKDIR,
                           capture_output=True, timeout=120)
        out = (r.stdout or b"") + (r.stderr or b"")
        text = out.decode("utf-8", errors="replace").strip()
        raw = text[:50000] if text else "(no output)"

        # Save full output to file for user inspection
        output_id = f"bash_{int(time.time() * 1000)}"
        output_path = TOOL_RESULTS_DIR / f"{output_id}.txt"
        if not TOOL_RESULTS_DIR.exists():
            TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path.write_text(raw, encoding="utf-8", errors="replace")

        # Return preview + expand hint
        rendered = render_bash_result(command, raw[:3000], r.returncode)
        total_lines = raw.count('\n') + 1
        if len(raw) > 3000 or total_lines > 20:
            rendered += (
                f"\n\033[90m[{total_lines} lines total] "
                f"[expand: {output_path}]\033[0m"
            )
        return rendered
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
