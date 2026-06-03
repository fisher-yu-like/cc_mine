from pathlib import Path
import subprocess
from config import WORKDIR
from tools.result_renderer import render_bash_result


def run_bash(command: str, cwd: Path = None,
             run_in_background: bool = False) -> str:
    # run_in_background is consumed by the dispatcher; direct execution ignores it.
    try:
        r = subprocess.run(command, shell=True, cwd=cwd or WORKDIR,
                           capture_output=True, timeout=120)
        out = (r.stdout or b"") + (r.stderr or b"")
        text = out.decode("utf-8", errors="replace").strip()
        raw = text[:50000] if text else "(no output)"
        return render_bash_result(command, raw, r.returncode)
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"