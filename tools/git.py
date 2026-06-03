import subprocess
from  config import  WORKDIR
def run_git(args: list[str]) -> tuple[bool, str]:
    try:
        # 使用 ["git"] + args 拼接命令，不使用 shell=True，天然防御命令注入攻击
        r = subprocess.run(["git"] + args, cwd=WORKDIR,
                           capture_output=True, timeout=30)
        out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", errors="replace").strip()
        return r.returncode == 0, out[:5000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return False, "Error: git timeout"