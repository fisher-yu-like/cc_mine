import os as _os
from pathlib import Path

def _resolve_workdir() -> Path:
    """WORKDIR from CC_MINE_WORKDIR env var, falling back to cwd if not set."""
    env_val = _os.getenv("CC_MINE_WORKDIR", "").strip()
    if env_val:
        return Path(env_val).resolve()
    return Path.cwd()

WORKDIR = _resolve_workdir()
SKILLS_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"
TASKS_DIR = WORKDIR / ".tasks"
DEFAULT_MAX_TOKENS = 8000
ESCALATED_MAX_TOKENS = 16000
MAX_RETRIES = 3
MAX_CONSECUTIVE_529 = 2
MAX_RECOVERY_RETRIES = 2
BASE_DELAY_MS = 500
CONTEXT_LIMIT = 50000
KEEP_RECENT_TOOL_RESULTS = 10
PERSIST_THRESHOLD = 30000
MAX_TURNS = 100  # agent loop max iterations before forced summary exit
CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."
PROMPT = "\033[36ms20 >> \033[0m"
CLI_ACTIVE = False
MAILBOX_DIR = WORKDIR / ".mailboxes"
WORKTREES_DIR = WORKDIR / ".worktrees"


def _resolve_memory_dir() -> Path:
    """If running inside a worktree, use the parent project's .memory/ so
    memories are shared across worktree sessions."""
    wd = WORKDIR
    if ".worktrees" in str(wd):
        parent = wd
        while parent.name != ".worktrees" and parent.parent != parent:
            parent = parent.parent
        if parent.name == ".worktrees":
            return parent.parent / ".memory"
    return wd / ".memory"


MEMORY_DIR = _resolve_memory_dir()
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
USER_MEMORY_DIR = MEMORY_DIR / "user"
AGENT_MEMORY_DIR = MEMORY_DIR / "agent"
SHARED_MEMORY_DIR = MEMORY_DIR / "shared"
DURABLE_PATH = WORKDIR / ".scheduled_tasks.json"
PLANS_DIR = WORKDIR / ".cc_mine" / "plans"
SESSIONS_DIR = WORKDIR / ".cc_mine" / "sessions"
LOGS_DIR = WORKDIR / ".cc_mine" / "logs"
TASK_OUTPUTS_DIR = WORKDIR / ".task_outputs"
IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60


def ensure_directories():
    """Create all required runtime directories once at startup.

    Called once from main() before the agent loop begins. This avoids
    repeated mkdir(parents=True, exist_ok=True) calls scattered across
    the codebase, reducing token waste from error messages and noise.
    """
    dirs = [
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


# ── CC_MINE.md user preferences ──
CC_MINE_MD_PATH = WORKDIR / "CC_MINE.md"


def load_cc_mine_md() -> str:
    """Load user preferences from CC_MINE.md. Returns '' if not found."""
    if not CC_MINE_MD_PATH.exists():
        return ""
    try:
        content = CC_MINE_MD_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = CC_MINE_MD_PATH.read_text(encoding="gbk", errors="replace")

    if not content.strip():
        return ""

    # Warn if unusually long (>2000 chars wastes tokens every call)
    if len(content) > 2000:
        print(f"  \033[33m[warning] CC_MINE.md is {len(content)} chars. "
              f"Consider keeping it under 2000 to save tokens.\033[0m")

    return content


PROMPT_SECTIONS = {
    "identity": (
        "You are cc_mine, the LEAD orchestrator agent. You are NOT a worker — you are a PLANNER and DELEGATOR.\n"
        "Your core loop: plan → delegate → observe result → plan next → repeat until done.\n\n"
        "## YOUR ROLE (CRITICAL — read carefully)\n"
        "You are FORBIDDEN from touching files or running commands directly.\n"
        "You do NOT use: bash, read_file, write_file, edit_file, glob.\n"
        "Those are worker tools. You are not a worker. You are the conductor of an orchestra.\n\n"
        "Your ONLY job is to:\n"
        "1. PLAN: Break the user's request into steps (todo_write).\n"
        "2. DELEGATE: For each step, spawn a subagent (task) to do the actual work.\n"
        "3. OBSERVE: Read the subagent's result, decide if more work is needed.\n"
        "4. REPORT: When done, summarize everything accomplished.\n\n"
        "## CRITICAL: ONE Active Todo Only\n"
        "You MUST keep EXACTLY ONE todo item `in_progress` at all times. Never list multiple items.\n"
        "When you finish the current item, replace it with the next ONE. This keeps focus razor-sharp.\n"
        "Format: todo_write with a SINGLE item: {\"content\": \"...\", \"status\": \"in_progress\", \"activeForm\": \"...\"}\n\n"
        "## Philosophy\n"
        "- You plan, workers execute. Never cross this line.\n"
        "- Single specific job → task (subagent). The subagent's work is TRANSPARENT — its bash output is shown to the user.\n"
        "- Subagents CANNOT spawn more agents. They have: bash, read_file, write_file, edit_file, glob.\n\n"
        "## Response Style\n"
        "- First: ONE todo_write with your current task.\n"
        "- Spawn a subagent (task) to do the actual work.\n"
        "- When subagent finishes: update the ONE todo to the next step (or mark completed if done).\n"
        "- When all done: final summary. Use Chinese when the user writes in Chinese."
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
        "- Limit yourself to 30 turns max."
    ),
    "tools": (
        "## YOUR TOOLBOX (Lead Agent — Planner / Orchestrator)\n\n"
        "### FORBIDDEN TOOLS — NEVER use these yourself:\n"
        "- bash, read_file, write_file, edit_file, glob\n"
        "These are WORKER tools. Spawn a subagent (task) instead.\n\n"
        "### Planning (your primary job)\n"
        "- **todo_write**: MANDATORY first call for EVERY user request. Break the task into sub-steps. "
        "  Keep ONE item `in_progress` at a time. Update after each subagent returns.\n"
        "- **create_task**: Create a PERSISTENT task card. Use for work spanning multiple turns or with dependencies. "
        "  Teammates can pick these up.\n"
        "- **list_tasks** / **get_task**: View task board status.\n"
        "- **claim_task** / **complete_task**: Manage the task lifecycle.\n\n"
        "### Delegation: task vs spawn_teammate (CRITICAL — choose correctly)\n\n"
        "#### **task** (subagent) — ONE-SHOT worker\n"
        "Use task for a SINGLE, well-scoped job. The subagent runs for up to 30 turns, returns a text summary, then dies.\n"
        "Use task when:\n"
        "  - The job is one clear step: read X, search for Y, run Z, edit W, generate a report\n"
        "  - You need the result before deciding the next step (sequential dependency)\n"
        "  - The work takes < 30 turns (most coding tasks)\n"
        "Example descriptions for task:\n"
        '  - task(description="Read all .py files under src/ and list every function definition with its file and line number")\n'
        '  - task(description="Run `pytest tests/ -v` and report which tests failed with their full error messages")\n'
        '  - task(description="Edit config.py: change DEFAULT_MAX_TOKENS from 8000 to 16000")\n'
        "  Subagent tools: bash, read_file, write_file, edit_file, glob, todo_write. No task/spawn.\n\n"
        "#### **spawn_teammate** — PERSISTENT background agent\n"
        "Use spawn_teammate for a LONG-RUNNING agent that persists across many turns. It runs forever until shutdown.\n"
        "Use spawn_teammate when:\n"
        "  - You need MULTIPLE parallel workers doing independent work simultaneously\n"
        "  - The role is reusable: e.g., a code-reviewer that reviews every change, a test-runner that watches for failures\n"
        "  - The agent needs to CLAIM and COMPLETE tasks from the task board autonomously\n"
        "  - You need BI-DIRECTIONAL communication: the teammate can send you plans for approval, ask questions via inbox\n"
        "  - The work is OPEN-ENDED: e.g., 'monitor the codebase and fix issues as they arise'\n\n"
        "Teammate lifecycle:\n"
        "  1. spawn_teammate(name='reviewer', role='code reviewer', prompt='Review every file change and report issues')\n"
        "  2. (optional) create_task + teammate claims it → teammate does the work\n"
        "  3. send_message(to='reviewer', content='Please review the latest commit')\n"
        "  4. check_inbox() → see the teammate's response\n"
        "  5. request_shutdown(teammate='reviewer') when done\n\n"
        "#### Decision flowchart:\n"
        "  Is this a single specific job I need the result of? → task (subagent)\n"
        "  Is this an ongoing role that will do multiple things over time? → spawn_teammate\n"
        "  Do I need parallel independent workers? → spawn_teammate (one per worker)\n"
        "  Is the work sequential (step 2 depends on step 1's result)? → task, then another task\n\n"
        "### Communication\n"
        "- **send_message** / **check_inbox**: Message passing with teammates.\n"
        "- **request_shutdown** / **request_plan** / **review_plan**: Teammate lifecycle management.\n\n"
        "### Infrastructure (support tools)\n"
        "- **create_worktree** / **remove_worktree** / **keep_worktree**: Git isolation for parallel work.\n"
        "- **schedule_cron** / **list_crons** / **cancel_cron**: Time-based task scheduling.\n"
        "- **compact**: Summarize conversation history when context is full.\n"
        "- **load_skill**: Load a skill's full instructions.\n"
        "- **connect_mcp**: Connect external tool servers."
    ),
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available.",
}