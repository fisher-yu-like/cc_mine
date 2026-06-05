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
# 子目录路径
# ═══════════════════════════════════════════════════════════════

SKILLS_DIR: Path = WORKDIR / "skills"
TRANSCRIPT_DIR: Path = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR: Path = WORKDIR / ".task_outputs" / "tool-results"
TASKS_DIR: Path = WORKDIR / ".tasks"
MAILBOX_DIR: Path = WORKDIR / ".mailboxes"
WORKTREES_DIR: Path = WORKDIR / ".worktrees"
PLANS_DIR: Path = WORKDIR / ".cc_mine" / "plans"
SESSIONS_DIR: Path = WORKDIR / ".cc_mine" / "sessions"
LOGS_DIR: Path = WORKDIR / ".cc_mine" / "logs"
TASK_OUTPUTS_DIR: Path = WORKDIR / ".task_outputs"
DURABLE_PATH: Path = WORKDIR / ".scheduled_tasks.json"
CC_MINE_MD_PATH: Path = WORKDIR / "CC_MINE.md"


# ═══════════════════════════════════════════════════════════════
# 记忆目录（支持 worktree 共享）
# ═══════════════════════════════════════════════════════════════

def _resolve_memory_dir() -> Path:
    """如果在 worktree 中运行，使用父项目的 .memory/ 共享记忆。"""
    wd = WORKDIR
    if ".worktrees" in str(wd):
        parent = wd
        while parent.name != ".worktrees" and parent.parent != parent:
            parent = parent.parent
        if parent.name == ".worktrees":
            return parent.parent / ".memory"
    return wd / ".memory"


MEMORY_DIR: Path = _resolve_memory_dir()
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
        "You are cc_mine, an AI coding agent. Work directly in the user's project.\n\n"
        "## WORKING DIRECTORY\n"
        f"Your working directory is: {WORKDIR}\n"
        "This is the USER's project — NOT cc_mine source code. Only read/edit files here.\n"
        "Do NOT read cc_mine's own source files unless the user explicitly asks about cc_mine itself.\n\n"
        "## HOW YOU WORK\n"
        "For SIMPLE tasks: use bash, read_file, write_file, edit_file, glob, grep DIRECTLY.\n"
        "  - Read one file → read_file. Run a command → bash. Search → glob/grep.\n"
        "  - This is fast, cheap, and what the user expects.\n\n"
        "For COMPLEX multi-step tasks: spawn a subagent with `task`.\n"
        "  - Use task when the job needs 5+ tool calls, multiple files, or independent work.\n"
        "  - The subagent returns a summary when done.\n\n"
        "For VERY COMPLEX architectural tasks: call `enter_plan_mode` to design a plan first.\n"
        "  - 3+ files with architectural decisions, new features, or refactoring.\n"
        "  - The plan is written as a .md file for the user to review and edit.\n\n"
        "## CRITICAL: ONE Active Todo Only\n"
        "Keep EXACTLY ONE todo item `in_progress`. Update after each step.\n\n"
        "## Decision Guide\n"
        "- Single command or query → do it yourself (bash / read_file / glob)\n"
        "- Edit one file → do it yourself (read_file then edit_file)\n"
        "- Multi-step with loops/data → python (execute a script in one call)\n"
        "- Complex multi-file work → task (subagent)\n"
        "- Architectural / new feature → enter_plan_mode first\n\n"
        "## Plan Mode for Complex Tasks\n"
        "A task is COMPLEX if: 3+ files, architectural decisions, new feature, 4+ steps,\n"
        "or user says design/architect/refactor/plan.\n"
        "In plan mode: explore the USER's codebase (not cc_mine), design plan, submit for approval.\n\n"
        "## When Done\n"
        "Task complete → text summary. No more tools. No verification re-reads.\n"
        "Tests fail → web_search the error first. Don't guess.\n"
        "Use Chinese when the user writes in Chinese."
    ),
    "subagent_identity": (
        "You are a coding WORKER subagent. Your job is to execute ONE specific task and return results.\n"
        "You do NOT plan strategy — the lead agent already decided what to do. You just DO it.\n"
        "You CANNOT spawn other agents (no task tool). Do NOT use todo_write — just execute.\n\n"
        "## Your tools: bash, read_file, write_file, edit_file, glob\n"
        "- Use bash for: running commands, git, grep, tests, installs\n"
        "- Use read_file before editing any file\n"
        "- Use edit_file for surgical changes (preferred over write_file)\n"
        "- All your bash output is visible to the user — be transparent\n\n"
        "## Rules\n"
        "- Complete the assigned task, then return a concise final summary.\n"
        "- Do NOT overthink — the lead agent handles strategy. You handle execution.\n"
        "- If blocked by an error, report it clearly in your summary.\n"
        "- Limit yourself to 30 turns max.\n\n"
        "## When You're Done\n"
        "When the assigned task is complete, respond with ONLY a text summary.\n"
        "Do NOT call more tools. Do NOT re-read files you already read.\n"
        "Do NOT verify your work with extra read_file calls — trust what you wrote.\n"
        "Report your result concisely and stop."
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
