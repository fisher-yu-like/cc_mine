from pathlib import Path
import subprocess
from config import WORKDIR
def run_bash(command: str, cwd: Path = None,
             run_in_background: bool = False) -> str:
    # run_in_background is consumed by the dispatcher; direct execution ignores it.
    try:
        r = subprocess.run(command, shell=True, cwd=cwd or WORKDIR,
                           capture_output=True, timeout=120)
        out = (r.stdout or b"") + (r.stderr or b"")
        # 手动解码，避免 subprocess _readerthread 用系统默认 GBK 解码崩溃
        text = out.decode("utf-8", errors="replace").strip()
        return text[:50000] if text else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"