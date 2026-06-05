import json
import os

from MessageBus import BUS
from config import IDLE_POLL_INTERVAL, IDLE_TIMEOUT, WORKDIR, TASKS_DIR, PROMPT_SECTIONS
from tool_registry import call_tool_handler
from hooks import trigger_hooks
from task import can_start
from tools.bash import run_bash
from tools.file_ops import run_read, run_write, run_edit, run_glob
from call_llm import get_client as _get_client
# ── Subagent Tool (OpenAI Format) ──

# 子智能体的系统提示词，从 config 注入统一的 subagent 身份
SUB_SYSTEM = PROMPT_SECTIONS.get("subagent_identity", (
    f"You are a coding subagent at {WORKDIR}. "
    "Complete the task, then return a concise final summary. "
    "Do not spawn more agents."
))

# 核心重构：完全对齐 OpenAI Tools 规范的子智能体工具箱
SUB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                    "offset": {"type": "integer"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"}
                },
                "required": ["path", "old_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"}
                },
                "required": ["pattern"]
            }
        }
    },
]

# 5 tools only — removed todo_write to save tokens (subagent just executes)
SUB_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
}


def extract_text(content) -> str:
    """
    OpenAI 格式下，content 通常就是一个纯字符串。
    为了兼容不同的历史结构，如果不是字符串则强转。
    """
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


# ── 2. 工具调用判断函数 (OpenAI 适配版) ──
def has_tool_use(choice_message) -> bool:
    """
    检查 OpenAI 的 Choice Message 对象中是否包含有效的工具调用列表。
    """
    return bool(getattr(choice_message, "tool_calls", None))


# ── 3. 子智能体孵化主函数 (OpenAI 适配版) ──
def spawn_subagent(description: str) -> str:
    # ── Transparent: show subagent start ──
    short_desc = description[:80].replace('\n', ' ')
    print(f"\n  \033[36m╭─ SUBAGENT ─────────────────────────────\033[0m")
    print(f"  \033[36m│\033[0m \033[1m{short_desc}\033[0m")

    # 消息队列初始化 — inject working directory so subagent knows WHERE to work
    from config import WORKDIR
    context_prefix = (
        f"[Context] Working directory: {WORKDIR}\n"
        f"This is the USER's project — NOT cc_mine source code.\n"
        f"Only read/edit files under {WORKDIR}. Use absolute paths in tools.\n\n"
    )
    messages = [
        {"role": "system", "content": SUB_SYSTEM},
        {"role": "user", "content": context_prefix + description}
    ]

    from ErrorRecovery import RecoveryState, with_retry
    sub_state = RecoveryState()
    turn_count = 0

    for _ in range(30):
        turn_count += 1
        try:
            # 呼叫 OpenAI 接口（带重试 + 模型降级）
            response = with_retry(
                lambda: _get_client().chat.completions.create(
                    model=os.getenv("PRIMARY_MODEL"),
                    messages=messages,
                    tools=SUB_TOOLS,
                    max_tokens=4000,
                ), state=sub_state
            )
        except RuntimeError as e:
            # with_retry exceeded MAX_RETRIES
            return f"Subagent execution failed after retries: {str(e)}"
        except Exception as e:
            return f"Subagent execution failed on API error: {str(e)}"

        choice = response.choices[0].message
        tool_calls = choice.tool_calls

        # 核心合拢：将 assistant 回复（含 tool_calls）转为纯 dict 塞进历史
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

        # 【收尾条件】如果大模型这一轮不打算用工具了，说明任务已做完，直接打破循环
        if not tool_calls:
            break

        # 准备收集这一轮并行的所有工具执行结果
        for tool_call in tool_calls:
            t_name = tool_call.function.name
            t_id = tool_call.id
            # OpenAI 下发的 arguments 是纯文本字符串，必须明文用 json.loads 解包
            try:
                t_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                # Malformed JSON from LLM — log and skip this tool call
                t_args = {}
                output = f"JSON parse error: {e}"
                messages.append({
                    "role": "tool", "tool_call_id": t_id,
                    "name": t_name, "content": output
                })
                continue

            # ⚙️ 核心拦截点：前置钩子（AOP 面向切面设计）
            # 可以在这里做安全审计，例如：如果是 bash 工具且包含 "rm -rf"，直接拒绝
            blocked = trigger_hooks("PreToolUse", tool_call)

            if blocked:
                output = str(blocked)
            else:
                # 正常执行：通过路由表找到对应的真实本地 Python 函数
                handler = SUB_HANDLERS.get(t_name)
                output = call_tool_handler(handler, t_args, t_name)

                # ⚙️ 核心拦截点：后置钩子
                trigger_hooks("PostToolUse", tool_call, output)

            # 严格遵循 OpenAI 规范：每一条并行工具回执都必须是独立的 role: tool 消息
            messages.append({
                "role": "tool",
                "tool_call_id": t_id,
                "name": t_name,
                "content": str(output)
            })

    # ── 4. 最终状态提取 ──
    # 从最新的历史记录倒序查找，捞出子智能体最后留下的"临终遗言"（任务总结）
    for msg in reversed(messages):
        if msg["role"] == "assistant" and msg.get("content"):
            text = extract_text(msg["content"])
            if text:
                print(f"  \033[36m╰─ DONE ({turn_count} turns) ─────────────────────\033[0m\n")
                return text

    print(f"  \033[36m╰─ DONE ({turn_count} turns) ─────────────────────\033[0m\n")
    return "Subagent finished without a text summary."


# ── Async Subagent Support ──
import threading as _threading

_subagent_results: dict[str, str] = {}
_subagent_lock = _threading.Lock()
_subagent_counter = 0


def spawn_subagent_async(description: str) -> str:
    """Spawn a subagent in a background thread. Returns immediately with an ID.
    Results are collected via collect_subagent_results()."""
    global _subagent_counter
    _subagent_counter += 1
    sid = f"sub_{_subagent_counter:04d}"

    def _run():
        result = spawn_subagent(description)
        with _subagent_lock:
            _subagent_results[sid] = result

    _threading.Thread(target=_run, daemon=True).start()
    print(f"  \033[33m[async subagent] {sid} started\033[0m")
    return sid


def collect_subagent_results() -> list[dict]:
    """Poll completed async subagents. Returns list of notification dicts."""
    with _subagent_lock:
        ready = dict(_subagent_results)
        _subagent_results.clear()
    notifications = []
    for sid, result in ready.items():
        summary = result[:300] if len(result) > 300 else result
        print(f"  \033[32m[async subagent] {sid} completed\033[0m")
        notifications.append({
            "role": "user",
            "content": (
                f"<subagent_result>\n"
                f"  <id>{sid}</id>\n"
                f"  <summary>{summary}</summary>\n"
                f"</subagent_result>"
            )
        })
    return notifications