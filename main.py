"""
这是一个从零开始手写的简化版code agent，是在学习learn claude code教程后
底层逻辑采用与Claudecode基本相同的code agent，但在其基础上有所改进
鉴于作者学术尚浅，其采用的在harness中的方法在不断学习更新，同时会保留旧方法用于学习研究

注：本agent由于作者缺乏经济来源，只在deepseek-v4-pro模型，openai提供商下所创作
"""
import time
from pathlib import Path

import config
from CronScheduler import consume_cron_queue
from ErrorRecovery import RecoveryState, is_prompt_too_long_error
from ProtocolState import consume_lead_inbox
from bg_task import should_run_background, start_background_task
from call_llm import call_llm
from executor import BUILTIN_HANDLERS
from hooks import trigger_hooks
from mcp import assemble_tool_pool
from memory import inject_background_notifications, prepare_context, update_context, reactive_compact, compact_history
from skill_load import scan_skills
from tool_registry import call_tool_handler
import threading
import sys
from MessageBus import  BUS,terminal_print

from config import PROMPT, DEFAULT_MAX_TOKENS, ESCALATED_MAX_TOKENS, MAX_RECOVERY_RETRIES, MAX_TURNS

#主函数 agentloop
rounds_since_todo = 0
agent_lock = threading.Lock()
def agent_loop(messages:list,context:dict):
    """
    Args:
        messages: 传入的消息
         context: 上下文
    :
    """

    global rounds_since_todo
    tools,handers=assemble_tool_pool(BUILTIN_HANDLERS)#这是组装的toolpool用于储存工具调用工具包
    state=RecoveryState()#这是生命周期的状态
    max_tokens=DEFAULT_MAX_TOKENS
    turn_count = 0

    while True:
        turn_count += 1
        if turn_count == MAX_TURNS:
            messages.append({"role": "user",
                             "content": "[System] Turn limit reached. Summarize what was accomplished and stop."})
            print(f"  \033[31m[max turns] {MAX_TURNS} reached, requesting final summary\033[0m")
        if turn_count > MAX_TURNS:
            return  # soft exit after LLM had one turn to respond
        # 每次循环：插入周期性任务/后台工作，上下文，工具call
        fired=consume_cron_queue()#周期性任务
        for job in fired:
            messages.append({"role":"user","content":f"[Scheduled]{job.prompt}"})
            print(f"  \033[35m[cron inject] {job.prompt[:60]}\033[0m")
        inject_background_notifications(messages)#插入后台任务
        if rounds_since_todo>=3:
            #每三次更新todolist状态
            messages.append({"role": "user",
                             "content": "<reminder>Update your todos.</reminder>"})
            rounds_since_todo=0
        prepare_context(messages)#提前准备好的上下文插入messages
        context=update_context(context,messages)
        tools,handlers=assemble_tool_pool(BUILTIN_HANDLERS)#每次循环更新toolpool

        try:
            #呼叫大脑
            response=call_llm(messages,context,tools,state,max_tokens)
        except Exception as e:
            if is_prompt_too_long_error(e) and not state.has_attempted_reactive_compact:
                # 处理错误，这是最开始的，之后我想多增加错误的处理，可能新开个文件
                messages[:] = reactive_compact(messages)
                state.has_attempted_reactive_compact = True
                continue
            messages.append({"role": "assistant", "content": f"[Error] {type(e).__name__}: {e}"})
            return
        choice = response.choices[0]
        message = choice.message
        #处理异常
        if choice.finish_reason == "length":
            if not state.has_escalated:
                max_tokens=ESCALATED_MAX_TOKENS
                state.has_escalated=True
                print(f"  \033[33m[max_tokens] retry with {max_tokens}\033[0m")
                continue
            messages.append({"role": "assistant", "content": message.content or ""})
            if state.recovery_count<MAX_RECOVERY_RETRIES:
                messages.append({"role": "assistant", "content": message.content or ""})
                state.recovery_count+=1
                continue
            return
        #由于可能改变
        max_tokens=DEFAULT_MAX_TOKENS
        state.has_escalated=False
        # 将 OpenAI ChatCompletionMessage + tool_calls 深转为纯 dict
        assistant_msg = {"role": "assistant", "content": message.content or ""}
        raw_tool_calls = getattr(message, "tool_calls", None)
        if raw_tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": getattr(tc, "type", "function"),
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                }
                for tc in raw_tool_calls
            ]
        messages.append(assistant_msg)
    #没有工具调用就返回
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            trigger_hooks("Stop", messages)
            return
        #接下来都是有工具调用的结果


        compacted_now = False
        tool_results_messages = []
        for tool_call in tool_calls:
            tool_name=tool_call.function.name
            tool_id=tool_call.id
            import json
            tool_input= json.loads(tool_call.function.arguments)

            # ── Plan mode: block write tools ──
            from planning import is_tool_allowed, get_state as plan_state
            if plan_state() == "planning" and not is_tool_allowed(tool_name):
                tool_results_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": tool_name,
                    "content": f"BLOCKED: planning mode is read-only. '{tool_name}' is not allowed. Use read_file, glob, web_search to explore, then submit_plan."
                })
                print(f"  \033[33m[plan block] {tool_name} (read-only mode)\033[0m")
                continue

            compat_block = {
                    "id": tool_id,
                    "name": tool_name,
                    "args": tool_input
                }
            #压缩上下文
            if tool_name=="compact":
                messages[:]=compact_history(messages)
                # 必须为 compact 的 tool_call 补上 tool 回执，否则 API 报错 insufficient tool messages
                tool_results_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": "compact",
                    "content": "Compaction succeeded. Context condensed."
                })
                compacted_now=True
                break
            #检查tool
            blocked=trigger_hooks("PreToolUse",compat_block)
            if blocked:
                tool_results_messages.append({
                    "role":"tool",
                    "tool_call_id":tool_id,
                    "name":tool_name,
                    "content":str(blocked)
                })
                continue
            #若耗时长经判断后在后台运行
            if should_run_background(tool_name,tool_input):
                bg_id=start_background_task(compat_block, handers)
                output=f"[Background task {bg_id} started] Result will arrive as a task_notification."
                tool_results_messages.append({
                    "role":"tool",
                    "tool_call_id":tool_id,
                    "name":tool_name,
                    "content":output
                })
                continue
            #执行器执行工具
            handler=handers.get(tool_name)
            output=call_tool_handler(handler,tool_input,tool_name)
            #tool使用后检查
            trigger_hooks("PostToolUse",compat_block,output)
            print(str(output)[:300])
            if tool_name == "todo_write":
                rounds_since_todo=0
            else:
                rounds_since_todo+=1

            tool_results_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "name": tool_name,
                            "content": str(output)
                        })


        if compacted_now:
            messages.extend(tool_results_messages)
            continue
        messages.extend(tool_results_messages)



def print_turn_assistants(messages: list, turn_start: int):
    # 切片只获取当前这一轮（Turn）新产生的所有消息
    for msg in messages[turn_start:]:
        # 过滤掉 user 和 tool 角色，只关心大模型的响应
        if msg.get("role") != "assistant":
            continue

        # OpenAI 格式下，msg["content"] 直接就是字符串（或者 None）
        content = msg.get("content")
        if content:
            terminal_print(content)
def cron_autorun_loop(history: list, context: dict):
    while True:
        time.sleep(1)
        fired = consume_cron_queue()
        if not fired:
            continue
        with agent_lock:
            turn_start = len(history)
            for job in fired:
                history.append({"role": "user",
                                "content": f"[Scheduled] {job.prompt}"})
                terminal_print(
                    f"  \033[35m[cron auto] {job.prompt[:60]}\033[0m")
            agent_loop(history, context)
            context.update(update_context(context, history))
            print_turn_assistants(history, turn_start)

if __name__ == "__main__":
    from log_setup import setup_logging, get_logger
    setup_logging(config.WORKDIR)
    log = get_logger()
    log.info(f"=== cc_mine starting | workdir={config.WORKDIR} ===")

    from planning import init_planning
    init_planning(config.WORKDIR)

    import argparse
    ap = argparse.ArgumentParser(description="cc_mine - a Claude Code clone")
    ap.add_argument("--workdir", default=None, help="Working directory")
    ap.add_argument("--model", default=None, help="Override PRIMARY_MODEL")
    ap.add_argument("--resume", default=None, help="Resume a saved session by ID")
    ap.add_argument("--session-label", default="", help="Label for auto-saved session")
    args = ap.parse_args()

    if args.workdir:
        config.WORKDIR = Path(args.workdir).resolve()
    if args.model:
        import os
        os.environ["PRIMARY_MODEL"] = args.model

    # ── Session resume ──
    from session import load_session, latest_session, save_session
    session_id = args.resume
    if session_id is None and not args.resume:
        # If no explicit --resume, check for latest
        latest = latest_session(config.WORKDIR)
        if latest:
            print(f"Latest session: {latest}")
            choice = input("Resume? [Y/n] ").strip().lower()
            if choice in ("", "y", "yes"):
                session_id = latest

    #运行主函数
    config.CLI_ACTIVE = True
    print(f"cc_mine agent  |  workdir: {config.WORKDIR}\n")
    print("Multi-line input: type your message, press Enter twice (empty line) to send.")
    print("Type q/exit to quit.\n")
    scan_skills()
    history = []#记录
    context = update_context({}, [])#更新上下文

    # Resume from saved session
    if session_id:
        loaded = load_session(config.WORKDIR, session_id)
        if loaded:
            history, ctx = loaded
            context.update(ctx)
            print(f"  \033[32m[session] resumed {session_id}: {len(history)} messages restored\033[0m\n")

    threading.Thread(target=cron_autorun_loop,
                     args=(history, context), daemon=True).start()#开辟线程运行上次没结束的task

    # Auto-save session ID
    _auto_session_id = session_id or f"session_{int(time.time())}"

    def read_multiline() -> str | None:
        """Read multi-line input. Empty line submits. Returns None on quit."""
        lines = []
        first = True
        while True:
            try:
                prompt = "\033[36ms01 >> \033[0m" if first else "\033[36m...    \033[0m"
                line = input(prompt)
            except (EOFError, KeyboardInterrupt):
                return None
            if first and line.strip().lower() in ("q", "exit"):
                return None
            if first and line.strip() == "":
                return None  # empty first line = quit
            if not first and line.strip() == "":
                break  # empty continuation line = submit
            if first and line.strip():
                lines.append(line.strip())
                first = False
            elif not first:
                lines.append(line)
        return "\n".join(lines) if lines else None

    while True:
        query = read_multiline()
        if query is None:
            break
        if query.strip() == "":
            continue
        trigger_hooks("UserPromptSubmit", query)
        #检查输入的prompt
        turn_start = len(history)
        history.append({"role": "user", "content": query})
        with agent_lock:
            agent_loop(history, context)
            context = update_context(context, history)
            print_turn_assistants(history, turn_start)
            # Auto-save after each completed turn
            save_session(history, context, config.WORKDIR, _auto_session_id,
                         args.session_label)

        inbox = consume_lead_inbox(route_protocol=True)
        if inbox:
            def inbox_label(msg):
                req_id = msg.get("metadata", {}).get("request_id", "")
                suffix = f" req:{req_id}" if req_id else ""
                return f"{msg.get('type', 'message')}{suffix}"

            inbox_text = "\n".join(
                f"From {m['from']} [{inbox_label(m)}]: "
                f"{m['content'][:200]}" for m in inbox)
            history.append({"role": "user",
                            "content": f"[Inbox]\n{inbox_text}"})
        print()