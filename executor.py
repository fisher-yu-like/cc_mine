"""
Tool handlers for cc_mine — Layer 1 Atomic tools (~18 core handlers).

Layer 2 tools (worktree, cron, task-board, teammate mgmt, etc.) are
accessed via `bash` + CLI commands, eliminating dedicated handler code.
"""

from mcp import connect_mcp
from memory import compact_history, add_memory, search_memory
from skill_load import load_skill
from subagent import spawn_subagent, spawn_subagent_async
from task import create_task
from tools.bash import run_bash
from tools.file_ops import run_read, run_write, run_edit, run_glob
from tools.grep import run_grep
from tools.python_exec import run_python
from tools.todo_write import run_todo_write
from tools.web import run_web_search, run_web_fetch
from planning import enter_plan_mode, submit_plan, exit_plan_mode


# ── Handler wrappers ──

def run_compact(focus: str = "", messages: list | None = None) -> str:
    """LLM-initiated context compaction."""
    if messages is None or len(messages) <= 5:
        return "Context is still fresh (under 5 messages). No compaction needed."
    print(f"\n  \033[33m[Active Compaction] Agent requested memory consolidation.\033[0m")
    messages[:] = compact_history(messages)
    return "Compaction succeeded. Earlier conversation condensed."


def run_task(description: str, run_in_background: bool = False) -> str:
    """Spawn a one-shot subagent (sync or async)."""
    if run_in_background:
        return spawn_subagent_async(description)
    return spawn_subagent(description)


def run_create_task(subject: str, description: str = "",
                    blockedBy: list[str] | None = None) -> str:
    """Create a persistent task card."""
    task_obj = create_task(subject, description, blockedBy)
    deps = f" (blockedBy: {', '.join(blockedBy)})" if blockedBy else ""
    print(f"  \033[34m[create] {task_obj.subject}{deps}\033[0m")
    return f"Created {task_obj.id}: {task_obj.subject}{deps}"


# ── The core 18 handlers ──

BUILTIN_HANDLERS = {
    # File & Shell
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
    "grep": run_grep,
    # Web
    "web_search": run_web_search,
    "web_fetch": run_web_fetch,
    # Delegation
    "task": run_task,
    "create_task": run_create_task,
    # Planning
    "todo_write": run_todo_write,
    "enter_plan_mode": enter_plan_mode,
    "submit_plan": submit_plan,
    "exit_plan_mode": exit_plan_mode,
    # Context & Memory
    "compact": run_compact,
    "add_memory": lambda title, content, tags="": add_memory(title, content, tags),
    "search_memory": search_memory,
    # External
    "connect_mcp": connect_mcp,
    # Layer 3: Code execution
    "python": run_python,
}
