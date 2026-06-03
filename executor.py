from AutonomousAgent import spawn_teammate_thread
from CronScheduler import run_schedule_cron, run_list_crons, run_cancel_cron
from MessageBus import BUS
from ProtocolState import consume_lead_inbox, new_request_id, pending_requests, ProtocolState
from mcp import connect_mcp
from memory import compact_history, add_memory, search_memory, delete_memory
from skill_load import load_skill
from subagent import spawn_subagent, spawn_subagent_async
from task import create_task, list_tasks, get_task_json, claim_task, complete_task
from tools.bash import run_bash
from tools.file_ops import run_read, run_write, run_edit, run_glob
from tools.todo_write import run_todo_write
from tools.web import run_web_search, run_web_fetch
from call_llm import call_llm_structured
from ErrorRecovery import RecoveryState as _RS  # for structured_output handler
from planning import enter_plan_mode, submit_plan, exit_plan_mode, update_plan_step
from workflow import begin_workflow, add_workflow_node, seal_workflow, execute_workflow, workflow_status, cancel_workflow
from worktree import create_worktree, remove_worktree, keep_worktree


def run_create_task(subject: str, description: str = "",
                    blockedBy: list[str] | None = None) -> str:
    task = create_task(subject, description, blockedBy)
    deps = f" (blockedBy: {', '.join(blockedBy)})" if blockedBy else ""
    print(f"  \033[34m[create] {task.subject}{deps}\033[0m")
    return f"Created {task.id}: {task.subject}{deps}"


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks."
    return "\n".join(
        f"  {t.id}: {t.subject} [{t.status}]"
        + (f" (wt:{t.worktree})" if t.worktree else "")
        for t in tasks)


def run_get_task(task_id: str) -> str:
    try:
        return get_task_json(task_id)
    except FileNotFoundError:
        return f"Error: task {task_id} not found"

def run_claim_task(task_id: str) -> str:
    try:
        return claim_task(task_id, owner="agent")
    except FileNotFoundError:
        return f"Error: task {task_id} not found"

def run_complete_task(task_id: str) -> str:
    try:
        return complete_task(task_id)
    except FileNotFoundError:
        return f"Error: task {task_id} not found"

def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)


def run_compact(focus: str, messages: list) -> str:
    """
    大模型主动呼叫 compact 工具时触发
    """
    if len(messages) <= 5:
        return "Context is still fresh (under 5 messages). No compaction needed."

    print(f"\n  \033[33m[Active Compaction] Agent requested memory consolidation. Focus: {focus}\033[0m")

    # 核心魔法：直接复用你的 compact_history 算法！
    # 利用原位切片修改 [:]，把外面运行时的 messages 瞬间替换成你重组后的短队列
    messages[:] = compact_history(messages)

    # 满足 OpenAI 工具协议：必须返回一段纯文本回执给大模型
    return f"Compaction success. Earlier conversation condensed. Current focused context initialized."
def run_send_message(to: str, content: str) -> str:
    BUS.send("lead", to, content)
    return f"Sent to {to}"

def run_create_worktree(name: str, task_id: str = "") -> str:
    return create_worktree(name, task_id)

def run_remove_worktree(name: str, discard_changes: bool = False) -> str:
    return remove_worktree(name, discard_changes)

def run_keep_worktree(name: str) -> str:
    return keep_worktree(name)

def run_check_inbox() -> str:
    msgs = consume_lead_inbox(route_protocol=True)
    if not msgs:
        return "(inbox empty)"
    lines = []
    for m in msgs:
        meta = m.get("metadata", {})
        req_id = meta.get("request_id", "")
        tag = f" [{m['type']} req:{req_id}]" if req_id else f" [{m['type']}]"
        lines.append(f"  [{m['from']}]{tag} {m['content'][:200]}")
    return "\n".join(lines)

def run_connect_mcp(name: str) -> str:
    return connect_mcp(name)
def run_request_shutdown(teammate: str) -> str:
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id, type="shutdown",
        sender="lead", target=teammate,
        status="pending", payload="")
    BUS.send("lead", teammate, "Shut down.", "shutdown_request",
             {"request_id": req_id})
    return f"Shutdown request sent to {teammate}"


def run_request_plan(teammate: str, task: str) -> str:
    BUS.send("lead", teammate, f"Submit plan for: {task}", "message")
    return f"Asked {teammate} to submit a plan"


def run_review_plan(request_id: str, approve: bool,
                    feedback: str = "") -> str:
    state = pending_requests.get(request_id)
    if not state:
        return f"Request {request_id} not found"
    state.status = "approved" if approve else "rejected"
    BUS.send("lead", state.sender,
             feedback or ("Approved" if approve else "Rejected"),
             "plan_approval_response",
             {"request_id": request_id, "approve": approve})
    return f"Plan {'approved' if approve else 'rejected'}"

BUILTIN_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
    "todo_write": run_todo_write,
    "task": lambda description, run_in_background=False: (
        spawn_subagent_async(description) if run_in_background
        else spawn_subagent(description)
    ),
    "load_skill": load_skill,
    "compact": run_compact,  # <-- 自动补齐上一节遗留的上下文自适应坍缩处理器
    "create_task": run_create_task,
    "list_tasks": run_list_tasks,
    "get_task": run_get_task,
    "claim_task": run_claim_task,
    "complete_task": run_complete_task,
    "schedule_cron": run_schedule_cron,
    "list_crons": run_list_crons,
    "cancel_cron": run_cancel_cron,
    "spawn_teammate": run_spawn_teammate,
    "send_message": run_send_message,
    "check_inbox": run_check_inbox,
    "request_shutdown": run_request_shutdown,
    "request_plan": run_request_plan,
    "review_plan": run_review_plan,
    "create_worktree": run_create_worktree,
    "remove_worktree": run_remove_worktree,
    "keep_worktree": run_keep_worktree,
    "connect_mcp": run_connect_mcp,
    "web_search": run_web_search,
    "web_fetch": run_web_fetch,
    "add_memory": lambda title, content, tags="": add_memory(title, content, tags),
    "search_memory": search_memory,
    "delete_memory": delete_memory,
    "structured_output": lambda prompt, schema, strict=True:
        call_llm_structured(prompt, schema, _RS(), strict),
    "enter_plan_mode": enter_plan_mode,
    "submit_plan": submit_plan,
    "exit_plan_mode": exit_plan_mode,
    "update_plan_step": update_plan_step,
    "begin_workflow": begin_workflow,
    "add_workflow_node": lambda workflow_id, node_id, description, depends_on=None:
        add_workflow_node(workflow_id, node_id, description, depends_on or []),
    "seal_workflow": seal_workflow,
    "execute_workflow": lambda workflow_id, max_parallel=5:
        execute_workflow(workflow_id, max_parallel),
    "workflow_status": workflow_status,
    "cancel_workflow": cancel_workflow,
}
