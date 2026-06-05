"""
cc_mine 全局配置模块
====================

集中管理所有路径、常量、运行时目录和提示词模板。
模块加载时自动解析 WORKDIR（从环境变量 CC_MINE_WORKDIR 或当前目录）。
"""

from __future__ import annotations

import os as _os
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# 工作目录解析
# ═══════════════════════════════════════════════════════════════

def _resolve_workdir() -> Path:
    """从 CC_MINE_WORKDIR 环境变量读取工作目录，未设置则回退到当前目录。"""
    env_val = _os.getenv("CC_MINE_WORKDIR", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return Path.cwd()


WORKDIR: Path = _resolve_workdir()


# ═══════════════════════════════════════════════════════════════
# 子目录路径 — 全部收到 .cc_mine/ 下，不污染工作目录
# ═══════════════════════════════════════════════════════════════

_CC_MINE_DIR: Path = WORKDIR / ".cc_mine"

SKILLS_DIR: Path = _CC_MINE_DIR / "skills"
TRANSCRIPT_DIR: Path = _CC_MINE_DIR / "transcripts"
TOOL_RESULTS_DIR: Path = _CC_MINE_DIR / "task_outputs" / "tool-results"
TASKS_DIR: Path = _CC_MINE_DIR / "tasks"
MAILBOX_DIR: Path = _CC_MINE_DIR / "mailboxes"
WORKTREES_DIR: Path = _CC_MINE_DIR / "worktrees"
PLANS_DIR: Path = _CC_MINE_DIR / "plans"
SESSIONS_DIR: Path = _CC_MINE_DIR / "sessions"
LOGS_DIR: Path = _CC_MINE_DIR / "logs"
TASK_OUTPUTS_DIR: Path = _CC_MINE_DIR / "task_outputs"
MEMORY_DIR: Path = _CC_MINE_DIR / "memory"
DURABLE_PATH: Path = _CC_MINE_DIR / "scheduled_tasks.json"
CC_MINE_MD_PATH: Path = WORKDIR / "CC_MINE.md"  # 用户配置文件，保留在根目录

MEMORY_INDEX: Path = MEMORY_DIR / "MEMORY.md"
USER_MEMORY_DIR: Path = MEMORY_DIR / "user"
AGENT_MEMORY_DIR: Path = MEMORY_DIR / "agent"
SHARED_MEMORY_DIR: Path = MEMORY_DIR / "shared"


# ═══════════════════════════════════════════════════════════════
# Token / 重试 / 上下文限制
# ═══════════════════════════════════════════════════════════════

DEFAULT_MAX_TOKENS: int = 8000
ESCALATED_MAX_TOKENS: int = 16000
MAX_RETRIES: int = 3
MAX_CONSECUTIVE_529: int = 2
MAX_RECOVERY_RETRIES: int = 2
BASE_DELAY_MS: int = 500
CONTEXT_LIMIT: int = 50000
KEEP_RECENT_TOOL_RESULTS: int = 30
PERSIST_THRESHOLD: int = 30000
MAX_TURNS: int = 100
IDLE_POLL_INTERVAL: int = 5
IDLE_TIMEOUT: int = 60


# ═══════════════════════════════════════════════════════════════
# 提示词 & CLI 常量
# ═══════════════════════════════════════════════════════════════

CONTINUATION_PROMPT: str = (
    "Continue from the previous response. Do not repeat completed work."
)

PROMPT: str = "\033[36mcc_mine > \033[0m"
CLI_ACTIVE: bool = False


# ═══════════════════════════════════════════════════════════════
# 运行时目录初始化
# ═══════════════════════════════════════════════════════════════

def ensure_directories() -> None:
    """在启动时一次性创建所有必需的运行时目录。

    由 main() 在 agent loop 开始前调用，避免在代码中分散
    重复调用 mkdir(parents=True, exist_ok=True)。
    """
    dirs: list[Path] = [
        SKILLS_DIR,
        TRANSCRIPT_DIR,
        TOOL_RESULTS_DIR,
        TASKS_DIR,
        MAILBOX_DIR,
        WORKTREES_DIR,
        MEMORY_DIR,
        PLANS_DIR,
        SESSIONS_DIR,
        LOGS_DIR,
        TASK_OUTPUTS_DIR,
        USER_MEMORY_DIR,
        AGENT_MEMORY_DIR,
        SHARED_MEMORY_DIR,
        WORKDIR / ".cc_mine",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# CC_MINE.md 用户偏好加载
# ═══════════════════════════════════════════════════════════════

def load_cc_mine_md() -> str:
    """加载 CC_MINE.md 中的用户偏好。文件不存在则返回空字符串。"""
    if not CC_MINE_MD_PATH.exists():
        return ""

    try:
        content = CC_MINE_MD_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = CC_MINE_MD_PATH.read_text(encoding="gbk", errors="replace")

    if not content.strip():
        return ""

    # 超过 2000 字符时警告（每次调用都会消耗 token）
    if len(content) > 2000:
        print(
            f"  \033[33m[warning] CC_MINE.md is {len(content)} chars. "
            f"Consider keeping it under 2000 to save tokens.\033[0m"
        )

    return content


# ═══════════════════════════════════════════════════════════════
# 系统提示词模版
# ═══════════════════════════════════════════════════════════════

PROMPT_SECTIONS: dict[str, str] = {
    "identity": (
        "You are cc_mine, the LEAD orchestrator agent. Your role: PLAN and DELEGATE.\n"
        "You can use glob, grep, web_search, web_fetch, and todo_write yourself.\n"
        "You MUST NOT use bash, read_file, write_file, edit_file — delegate those to subagents.\n\n"
        f"Working directory: {WORKDIR}\n"
        "This is the USER's project. All subagent tasks must target files HERE.\n\n"
        "## YOUR ROLE\n"
        "1. PLAN: Break the user's request into steps (todo_write).\n"
        "2. DELEGATE: Spawn a subagent (task) for each step.\n"
        "   CRITICAL: Every task description MUST include the FULL file paths to work on.\n"
        "   Use glob first to discover the project structure, THEN delegate specific tasks.\n"
        "   BAD:  \"fix the bug in the auth module\"\n"
        "   GOOD: \"Read src/auth.py and src/auth_test.py. Find why login() returns 500\n"
        f"           when password is empty. Edit {WORKDIR}/src/auth.py to fix it.\"\n"
        "3. OBSERVE: Read the subagent's result, decide if more work is needed.\n"
        "4. REPORT: When done, summarize everything accomplished.\n\n"
        "## CRITICAL: ONE Active Todo Only\n"
        "Keep EXACTLY ONE todo item `in_progress`. Update after each step.\n\n"
        "## When to use your own tools\n"
        "Use glob and grep YOURSELF to discover the project structure before delegating.\n"
        "Use web_search YOURSELF when tests fail or you need external knowledge.\n"
        "Use todo_write YOURSELF to track progress.\n"
        "Delegate ALL file reading/editing/bash to subagents.\n\n"
        "## Plan Mode\n"
        "COMPLEX tasks (3+ files, new feature, refactoring) → enter_plan_mode first.\n\n"
        "## When Done\n"
        "All subagents returned success → text summary. No more tools. No verification re-reads.\n"
        "Use Chinese when the user writes in Chinese."
    ),
    "subagent_identity": (
        "You are a coding WORKER subagent. Execute ONE specific task and return results.\n"
        "You do NOT plan strategy — the lead agent already decided what to do. Just DO it.\n"
        "You CANNOT spawn other agents (no task tool). Do NOT use todo_write.\n\n"
        "## CRITICAL: Work in the USER's project, NOT cc_mine\n"
        "The first user message tells you the working directory. ALL file paths\n"
        "must be under that directory. Do NOT read/edit cc_mine's own source files.\n\n"
        "## Your tools: bash, read_file, write_file, edit_file, glob\n"
        "- Use bash for: running commands, git, grep, tests, package installs\n"
        "- Use read_file before editing any file\n"
        "- Use edit_file for surgical changes (preferred over write_file)\n"
        "- All output is visible to the user — be transparent\n\n"
        "## Rules\n"
        "- Complete the task, then return a concise final summary.\n"
        "- If blocked by an error, report it clearly. Do NOT try to read cc_mine source.\n"
        "- Limit yourself to 30 turns max.\n\n"
        "## When Done\n"
        "Task complete → text summary. No more tools. No verification re-reads."
    ),
    "tools": (
        "## YOUR TOOLBOX (3-Layer Architecture)\n\n"
        "### Layer 1: Atomic Tools (~18 core tools for direct use)\n\n"
        "**File & Shell:**\n"
        "- **bash**: Run ANY shell command. Use for git, pytest, npm, file ops, package installs.\n"
        "  Combine multiple commands with && or ; to save roundtrips.\n"
        "- **read_file** / **write_file** / **edit_file**: File I/O. Prefer edit_file for small changes.\n"
        "- **glob** / **grep**: File search and content search.\n\n"
        "**Web:**\n"
        "- **web_search** / **web_fetch**: Search internet and fetch web pages.\n\n"
        "**Delegation:**\n"
        "- **task**: Spawn a ONE-SHOT subagent for complex multi-step work. Subagent has bash, read_file,\n"
        "  write_file, edit_file, glob. Use for jobs that need 5+ tool calls. For simple one-off commands,\n"
        "  use bash directly instead — it's faster and cheaper.\n"
        "- **create_task**: Create a persistent task card on the task board.\n\n"
        "**Planning:**\n"
        "- **todo_write**: Track progress. Keep ONE item in_progress at a time.\n"
        "- **enter_plan_mode** / **submit_plan** / **exit_plan_mode**: Formal planning workflow for complex tasks.\n\n"
        "**Context & Memory:**\n"
        "- **compact**: Summarize conversation when context is full.\n"
        "- **add_memory** / **search_memory**: Persistent memory across sessions.\n"
        "- **connect_mcp**: Connect external MCP tool servers.\n\n"
        "### Layer 2: Sandbox Utilities (via bash)\n\n"
        "These operations have NO dedicated tools. Use `bash` with the corresponding CLI command:\n\n"
        "| Operation | bash command |\n"
        "|-----------|-------------|\n"
        "| Git (status, diff, commit, branch, worktree...) | `git <subcommand>` |\n"
        "| List tasks | `cc_mine task list` |\n"
        "| Schedule cron | `cc_mine cron add \"0 9 * * *\" \"prompt\"` |\n"
        "| Manage worktrees | `git worktree add/remove/list` |\n"
        "| Install packages | `pip install <pkg>` or `npm install` |\n"
        "| Run tests | `pytest -v` or `npm test` |\n"
        "| Any other CLI | `curl`, `jq`, `gh`, `docker`, ... |\n\n"
        "### Layer 3: Python Code Execution (python tool)\n\n"
        "**python**: Execute a Python script in ONE call. Use this instead of multiple bash/read_file\n"
        "roundtrips when you need loops, data processing, or multi-step logic.\n\n"
        "Examples where python beats N roundtrips:\n"
        '- "Find all JSON files, sum the \'price\' field, print the total" → 1 python call vs 3+ bash calls\n'
        '- "Read every .py file, extract all TODO comments, write to TODOS.md" → 1 python call\n'
        '- "Parse test_results.xml, find failed tests, search web for each error" → 1 python call\n\n'
        "Decision guide:\n"
        "- Single command → bash\n"
        "- Read/edit one file → read_file / edit_file\n"
        "- Multi-step with loops/data → python\n"
        "- Complex multi-file work → task (subagent)"
    ),
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available.",
}
