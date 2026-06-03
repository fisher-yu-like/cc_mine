import os
import threading
import json
import re
import time
from pathlib import Path

from MessageBus import BUS
from ProtocolState import ProtocolState, new_request_id, pending_requests
from config import WORKDIR, TASKS_DIR, IDLE_TIMEOUT, IDLE_POLL_INTERVAL, WORKTREES_DIR
from tool_registry import call_tool_handler
from task import can_start, list_tasks, claim_task, load_task, complete_task
from tools.bash import run_bash
from tools.file_ops import run_read, run_write
from call_llm import get_client as _get_client
SUB_SYSTEM = (
    f"You are a coding subagent at {WORKDIR}. "
    "Complete the task, then return a concise final summary. "
    "Do not spawn more agents."
)
def scan_unclaimed_tasks() -> list[dict]:
    unclaimed = []
    # 按照任务编号字典序，扫描任务文件夹下的所有 JSON 任务卡
    for f in sorted(TASKS_DIR.glob("task_*.json")):
        task = json.loads(f.read_text(encoding="utf-8"))
        # 严格过滤：必须是‘待处理’、‘没人认领’，且‘满足前置依赖可以开始’的任务
        if (task.get("status") == "pending"
                and not task.get("owner")
                and can_start(task["id"])):
            unclaimed.append(task)
    return unclaimed
active_teammates: dict[str, bool] = {}
def idle_poll(agent_name: str, messages: list,
              name: str, role: str,
              worktree_context: dict | None = None) -> str:
    # 总共可以睡几轮：比如 60 秒 / 5 秒 = 循环 12 次
    for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL):
        time.sleep(IDLE_POLL_INTERVAL)  # 每次闭眼睡 5 秒，防止把 CPU 烧满

        inbox = BUS.read_inbox(agent_name)  # 读取消息总线
        if inbox:
            for msg in inbox:
                # 拦截最高指令：如果是主控端发来的下班/关机请求
                if msg.get("type") == "shutdown_request":
                    req_id = msg.get("metadata", {}).get("request_id", "")
                    # 严格走完握手协议：往消息总线回执“已收到，批准下班”
                    BUS.send(name, "lead", "Shutting down.",
                             "shutdown_response",
                             {"request_id": req_id, "approve": True})
                    return "shutdown"  # 触发注销状态

            # 如果收件箱里不是关机指令，而是普通工作讨论或新活计
            # 完美适配 OpenAI 格式：组装成一条标准的 user 消息，塞进历史记忆
            messages.append({"role": "user",
                             "content": "<inbox>" + json.dumps(inbox) + "</inbox>"})
            return "work"  # 被动唤醒，开始进入大模型思考循环处理新消息



# ── Teammate Spawner (OpenAI Format) ──

def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    if name in active_teammates:
        return f"Teammate '{name}' already exists"

    # 计划流转上下文：控制审批闸门
    protocol_ctx = {"waiting_plan": None}

    def handle_inbox_message(name: str, msg: dict, messages: list):
        msg_type = msg.get("type", "message")
        meta = msg.get("metadata", {})
        req_id = meta.get("request_id", "")

        if msg_type == "shutdown_request":
            BUS.send(name, "lead", "Shutting down.",
                     "shutdown_response",
                     {"request_id": req_id, "approve": True})
            return True

        if msg_type == "plan_approval_response":
            approve = meta.get("approve", False)
            if req_id == protocol_ctx["waiting_plan"]:
                protocol_ctx["waiting_plan"] = None
            # 严格对齐 OpenAI 的 user 消息回执
            messages.append({"role": "user",
                             "content": "[Plan approved]" if approve else f"[Plan rejected] {msg['content']}"})
        return False

    def run():
        wt_ctx = {"path": None}

        def _wt_cwd():
            p = wt_ctx["path"]
            return Path(p) if p else None

        # 动态工作目录感知工具（原样保留逻辑）
        def _run_bash(command: str) -> str:
            return run_bash(command, cwd=_wt_cwd())

        def _run_read(path: str) -> str:
            return run_read(path, cwd=_wt_cwd())

        def _run_write(path: str, content: str) -> str:
            return run_write(path, content, cwd=_wt_cwd())

        def _run_list_tasks():
            tasks = list_tasks()
            if not tasks: return "No tasks."
            return "\n".join(
                f"  {t.id}: {t.subject} [{t.status}]" + (f" (wt:{t.worktree})" if t.worktree else "")
                for t in tasks)

        def _run_claim_task(task_id: str):
            result = claim_task(task_id, owner=name)
            if "Claimed" in result:
                task = load_task(task_id)
                wt_ctx["path"] = str(WORKTREES_DIR / task.worktree) if task.worktree else None
            return result

        def _run_complete_task(task_id: str):
            result = complete_task(task_id)
            wt_ctx["path"] = None
            return result

        # ── 1. 适配 OpenAI 的消息队列初始化 ──
        # OpenAI 要求系统人格必须作为 messages 的首条 system 消息传入
        messages = [
            {"role": "system",
             "content": f"You are '{name}', a {role}. Use tools to complete tasks. If a task has a worktree, work in that directory. {SUB_SYSTEM}"},
            {"role": "user", "content": prompt}
        ]

        # ── 2. 适配 OpenAI 的 Tools Schema ──
        sub_tools = [
            {"type": "function", "function": {"name": "bash", "description": "Run a shell command.",
                                              "parameters": {"type": "object",
                                                             "properties": {"command": {"type": "string"}},
                                                             "required": ["command"]}}},
            {"type": "function", "function": {"name": "read_file", "description": "Read file.",
                                              "parameters": {"type": "object",
                                                             "properties": {"path": {"type": "string"},
                                                                            "limit": {"type": "integer"},
                                                                            "offset": {"type": "integer"}},
                                                             "required": ["path"]}}},
            {"type": "function", "function": {"name": "write_file", "description": "Write file.",
                                              "parameters": {"type": "object",
                                                             "properties": {"path": {"type": "string"},
                                                                            "content": {"type": "string"}},
                                                             "required": ["path", "content"]}}},
            {"type": "function", "function": {"name": "send_message", "description": "Send message to another agent.",
                                              "parameters": {"type": "object", "properties": {"to": {"type": "string"},
                                                                                              "content": {
                                                                                                  "type": "string"}},
                                                             "required": ["to", "content"]}}},
            {"type": "function", "function": {"name": "submit_plan", "description": "Submit a plan for Lead approval.",
                                              "parameters": {"type": "object",
                                                             "properties": {"plan": {"type": "string"}},
                                                             "required": ["plan"]}}},
            {"type": "function", "function": {"name": "list_tasks", "description": "List all tasks.",
                                              "parameters": {"type": "object", "properties": {}, "required": []}}},
            {"type": "function", "function": {"name": "claim_task", "description": "Claim a pending task.",
                                              "parameters": {"type": "object",
                                                             "properties": {"task_id": {"type": "string"}},
                                                             "required": ["task_id"]}}},
            {"type": "function",
             "function": {"name": "complete_task", "description": "Mark an in-progress task as completed.",
                          "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}},
                                         "required": ["task_id"]}}}
        ]

        sub_handlers = {
            "bash": _run_bash, "read_file": _run_read, "write_file": _run_write,
            "send_message": lambda to, content: (BUS.send(name, to, content), "Sent")[1],
            "list_tasks": _run_list_tasks, "claim_task": _run_claim_task, "complete_task": _run_complete_task,
        }

        while True:
            # 维持身份锚定
            if len(messages) <= 3:
                messages.insert(1, {"role": "user",
                                    "content": f"<identity>You are '{name}', role: {role}. Continue your work.</identity>"})

            should_shutdown = False
            for _ in range(10):
                inbox = BUS.read_inbox(name)
                for msg in inbox:
                    if handle_inbox_message(name, msg, messages):
                        should_shutdown = True
                        break
                if should_shutdown: break

                # ── 3. 拦截点：计划尚未通过，挂起模型思考 ──
                if protocol_ctx["waiting_plan"]:
                    time.sleep(IDLE_POLL_INTERVAL)
                    continue

                if inbox and not should_shutdown:
                    non_protocol = [m for m in inbox if m.get("type") == "message"]
                    if non_protocol:
                        messages.append({"role": "user", "content": f"<inbox>{json.dumps(non_protocol)}</inbox>"})

                # ── 4. API 呼叫（带重试 + 模型降级） ──
                from ErrorRecovery import RecoveryState as _RS, with_retry as _wr
                _tm_state = getattr(run, '_recovery_state', None)
                if _tm_state is None:
                    _tm_state = _RS()
                    run._recovery_state = _tm_state  # persist across loop iterations

                consecutive_errors = getattr(run, '_consecutive_errors', 0)
                try:
                    response = _wr(
                        lambda: _get_client().chat.completions.create(
                            model=os.getenv("PRIMARY_MODEL"),
                            messages=messages[-20:],
                            tools=sub_tools,
                            max_tokens=4000,
                        ), state=_tm_state
                    )
                    run._consecutive_errors = 0  # reset on success
                except Exception as e:
                    run._consecutive_errors = consecutive_errors + 1
                    err_msg = f"[Teammate {name}] API error (#{run._consecutive_errors}): {type(e).__name__}: {str(e)[:100]}"
                    print(f"  \033[31m{err_msg}\033[0m")
                    if run._consecutive_errors >= 3:
                        BUS.send(name, "lead",
                                 f"Teammate shutting down after {run._consecutive_errors} consecutive API errors: {str(e)[:100]}",
                                 "error")
                        print(f"  \033[31m[Teammate {name}] shutting down after {run._consecutive_errors} errors\033[0m")
                        break
                    time.sleep(2 ** consecutive_errors)  # backoff
                    continue

                choice = response.choices[0].message
                tool_calls = choice.tool_calls

                # 将 assistant 回复（含 tool_calls）深转为纯 dict 塞进历史
                messages.append({
                    "role": "assistant",
                    "content": choice.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": getattr(tc, "type", "function"),
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in (tool_calls or [])
                    ] if tool_calls else None
                })

                # 如果模型没有提出任何工具调用，打破执行链，交还控制权
                if not tool_calls:
                    break

                # ── 5. 适配 OpenAI 的多工具并行回执拼装 ──
                for tool_call in tool_calls:
                    t_name = tool_call.function.name
                    t_id = tool_call.id
                    # OpenAI 的参数需要明文使用 json.loads 反序列化
                    t_args = json.loads(tool_call.function.arguments)

                    if t_name == "submit_plan":
                        output = _teammate_submit_plan(name, t_args.get("plan", ""))
                        match = re.search(r"\((req_\d+)\)", output)
                        protocol_ctx["waiting_plan"] = match.group(1) if match else output
                    else:
                        handler = sub_handlers.get(t_name)
                        output = call_tool_handler(handler, t_args, t_name)

                    # 严格遵循 OpenAI 规范：每一条工具回执必须使用独立的 role: tool 结构，并对齐 tool_call_id
                    messages.append({
                        "role": "tool",
                        "tool_call_id": t_id,
                        "name": t_name,
                        "content": str(output)
                    })

                    if protocol_ctx["waiting_plan"]:
                        break  # 如果模型提交了方案，后续的并行工具调用立刻作废，原地等待审批

                if protocol_ctx["waiting_plan"]:
                    break

            if should_shutdown: break
            if protocol_ctx["waiting_plan"]: continue

            # 进入空闲睡眠状态
            idle_result = idle_poll(name, messages, name, role, wt_ctx)
            if idle_result in ("shutdown", "timeout"):
                break

        # ── 6. 适配 OpenAI 的最终摘要提取 ──
        summary = "Done."
        for msg in reversed(messages):
            if msg["role"] == "assistant" and msg.get("content"):
                summary = msg["content"]
                break

        BUS.send(name, "lead", summary, "result")
        active_teammates.pop(name, None)

    active_teammates[name] = True
    threading.Thread(target=run, daemon=True).start()
    return f"Teammate '{name}' spawned as {role}"


def _teammate_submit_plan(from_name: str, plan: str) -> str:
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id, type="plan_approval",
        sender=from_name, target="lead",
        status="pending", payload=plan)
    BUS.send(from_name, "lead", plan,
             "plan_approval_request",
             {"request_id": req_id})
    return f"Plan submitted ({req_id})"