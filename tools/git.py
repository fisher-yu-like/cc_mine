import subprocess
from config import WORKDIR
from tools.result_renderer import render_git_result


def run_git(args: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(["git"] + args, cwd=WORKDIR,
                           capture_output=True, timeout=30)
        out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", errors="replace").strip()
        raw = out[:5000] if out else "(no output)"
        success = r.returncode == 0
        return success, render_git_result(args, raw, success)
    except subprocess.TimeoutExpired:
        return False, "Error: git timeout"