"""
这是一个从零开始手写的简化版code agent，是在学习learn claude code教程后
底层逻辑采用与Claudecode基本相同的code agent，但在其基础上有所改进
鉴于作者学术尚浅，其采用的在harness中的方法在不断学习更新，同时会保留旧方法用于学习研究

注：本agent由于作者缺乏经济来源，只在deepseek-v4-pro模型，openai提供商下所创作
"""
import time
from pathlib import Path

import config
import cli_commands
from CronScheduler import consume_cron_queue
from ErrorRecovery import RecoveryState, is_prompt_too_long_error
from ProtocolState import consume_lead_inbox
from bg_task import should_run_background, start_background_task
from call_llm import call_llm
from executor import BUILTIN_HANDLERS
from hooks import trigger_hooks
from mcp import assemble_tool_pool
from memory import inject_background_notifications, prepare_context, update_context, reactive_compact, compact_history
from multimodal import drain_pending, has_pending, pending_count
from skill_load import scan_skills
from terminal_renderer import render_assistant, render_info, render_error, render_tool_output
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

    # ── Repetition guard state ──
    tool_call_history: list[dict] = []
    READONLY_TOOLS = {"read_file", "glob", "grep", "web_search", "web_fetch"}
    _guard_repetition_fired = False
    _guard_readonly_fired = False

    def _args_fingerprint(t_name: str, t_input: dict) -> str:
        """Stable fingerprint for detecting repeated identical tool calls."""
        if t_name == "read_file":
            return f"read:{t_input.get('path', '')}"
        elif t_name == "bash":
            return f"bash:{str(t_input.get('command', ''))[:100]}"
        elif t_name in ("glob", "grep"):
            return f"{t_name}:{t_input.get('pattern', '')}"
        elif t_name == "web_search":
            return f"search:{str(t_input.get('query', ''))[:80]}"
        else:
            import json as _json
            return f"{t_name}:{_json.dumps(t_input, sort_keys=True)[:200]}"

    def _describe_tool(t_name: str, t_input: dict) -> str:
        """Human-readable one-liner for TUI display."""
        if t_name == "bash":
            return f"$ {str(t_input.get('command', ''))[:80]}"
        elif t_name == "read_file":
            return f"read {t_input.get('path', '?')}"
        elif t_name == "write_file":
            return f"write {t_input.get('path', '?')}"
        elif t_name == "edit_file":
            return f"edit {t_input.get('path', '?')}"
        elif t_name == "glob":
            return f"glob {t_input.get('pattern', '?')}"
        elif t_name == "grep":
            return f"grep {t_input.get('pattern', '?')}"
        elif t_name == "task":
            return f"spawn {t_input.get('description', '?')[:60]}"
        elif t_name in ("web_search", "web_fetch"):
            return f"{t_name} {str(t_input.get('query', t_input.get('url', '?')))[:60]}"
        else:
            return f"{t_name}"

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
            #每三次更新todolist状态，这是强制提醒
            messages.append({"role": "user",
                             "content": "<reminder>Update your todos.</reminder>"})
            rounds_since_todo=0
        prepare_context(messages)#提前准备好的上下文插入messages
        context=update_context(context,messages)
        tools,handlers=assemble_tool_pool(BUILTIN_HANDLERS)#每次循环更新toolpool

        # ── Repetition Guards ──
        if not _guard_repetition_fired and len(tool_call_history) >= 3:
            last_3 = tool_call_history[-3:]
            if all(t["name"] == last_3[0]["name"] for t in last_3):
                if all(t["args_key"] == last_3[0]["args_key"] for t in last_3):
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[System] You've called '{last_3[0]['name']}' 3 times "
                            f"in a row with the same arguments. If the task is "
                            f"complete, respond with a text summary and NO tool calls."
                        )
                    })
                    print(f"  \033[33m[repetition guard] {last_3[0]['name']} x3\033[0m")
                    _guard_repetition_fired = True

        if not _guard_readonly_fired and len(tool_call_history) >= 5:
            last_5 = tool_call_history[-5:]
            if all(t["name"] in READONLY_TOOLS for t in last_5):
                unique = sorted(set(t["name"] for t in last_5))
                messages.append({
                    "role": "user",
                    "content": (
                        f"[System] Your last 5 tool calls have all been read-only "
                        f"({', '.join(unique)}). Consider whether you have enough "
                        f"information to proceed or conclude. If done, respond "
                        f"without tool calls."
                    )
                })
                print(f"  \033[33m[readonly guard] 5 read-only calls\033[0m")
                _guard_readonly_fired = True

        try:
            #呼叫大脑
            response=call_llm(messages,context,tools,state,max_tokens)
        except Exception as e:
            if is_prompt_too_long_error(e) and not state.has_attempted_reactive_compact:
               
                messages[:] = reactive_compact(messages)
                state.has_attempted_reactive_compact = True
                continue

            # ── 错误恢复：保存进度，不丢失任务状态 ──
            short_msg = f"[Error] {type(e).__name__}: {str(e)[:200]}"
            messages.append({"role": "assistant", "content": short_msg})
            print(f"\n  \033[31m[agent error] {type(e).__name__}: {str(e)[:150]}\033[0m")
            print(f"  \033[33m[save] saving session before exit...\033[0m")
            try:
                from session import save_session as _ss
                _ss(messages, context, config.WORKDIR,
                    getattr(agent_loop, '_crash_session_id', f"crash_{int(time.time())}"),
                    f"auto-saved: {type(e).__name__}", crashed=True)
                print(f"  \033[32m[save] session saved. Restart to resume.\033[0m")
            except Exception:
                print(f"  \033[31m[save] failed to save session\033[0m")
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
                render_info(f"[plan block] {tool_name} (read-only mode)")
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
            #执行器执行工具（整体 try/except 保护 —— 任何工具崩溃都不中断 agent）
            from spinner import set_state, SpinnerState
            set_state(SpinnerState.RUNNING_TOOL, tool_name)
            try:
                handler=handers.get(tool_name)
                output=call_tool_handler(handler,tool_input,tool_name)
                #tool使用后检查
                trigger_hooks("PostToolUse",compat_block,output)

                # ── Record tool call for repetition guard ──
                tool_call_history.append({
                    "name": tool_name,
                    "args_key": _args_fingerprint(tool_name, tool_input)
                })
                _guard_repetition_fired = False
                _guard_readonly_fired = False

                # ── Store output + render ──
                from output_manager import store_output, get_collapse_default
                full_text = str(output)
                output_idx = store_output(tool_name, full_text)

                # ── TUI mode: render to full-screen Live layout ──
                from tui import get_active as tui_active
                _tui = tui_active()
                if _tui:
                    desc = _describe_tool(tool_name, tool_input)
                    _tui.render_tool(tool_name, desc, full_text)
                    _tui.set_status(f"Running... [{tool_name}] {desc[:50]}")

                # ── Inline render (only when not in TUI mode) ──
                collapsed = get_collapse_default()
                if not _tui:
                    if collapsed:
                        preview = '\n'.join(full_text.split('\n')[:8])
                        render_tool_output(tool_name, preview,
                                           collapsed=True, output_index=output_idx - 1,
                                           full_output=full_text)
                    else:
                        render_tool_output(tool_name, full_text,
                                           collapsed=False, output_index=output_idx - 1)

                # ── Test failure detection (non-zero exit code on test commands) ──
                if tool_name == "bash":
                    from tools.bash import get_last_exit_code
                    exit_code = get_last_exit_code()
                    cmd = tool_input.get("command", "")
                    from debug_tracker import is_test_command, extract_error_lines
                    if is_test_command(cmd) and exit_code != 0:
                        error_lines = extract_error_lines(full_text)
                        failure_key = f"test_failure:{cmd[:80]}"
                        from debug_tracker import (record_failure,
                                                   should_trigger_web_search,
                                                   get_failure_count)
                        record_failure(failure_key + "\n" + error_lines[:200])
                        if should_trigger_web_search():
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"[Auto Debug Search] Tests failed (exit {exit_code}). "
                                    f"Relevant errors:\n{error_lines[:500]}\n\n"
                                    f"Use web_search to find solutions for this specific "
                                    f"error. Search English + Chinese sources. Do NOT "
                                    f"guess — find the root cause before fixing."
                                )
                            })
                            print(f"  \033[35m[test failure] web search triggered\033[0m")
            except Exception as tool_err:
                output = f"[Tool Error] {type(tool_err).__name__}: {str(tool_err)[:200]}"
                render_error(f"tool crash: {tool_name}: {type(tool_err).__name__}")
                from log_setup import error
                error(f"Tool '{tool_name}' crashed: {type(tool_err).__name__}: {str(tool_err)[:300]}")

                # Debug tracking: record failures for auto web-search
                from debug_tracker import (is_debug_context, record_failure,
                                           should_trigger_web_search,
                                           get_failure_count)
                last_user = ""
                for m in reversed(messages):
                    if m.get("role") == "user":
                        last_user = str(m.get("content", ""))
                        break
                if is_debug_context(last_user):
                    record_failure(str(output))
                    if should_trigger_web_search():
                        messages.append({
                            "role": "user",
                            "content": (
                                f"[Auto Debug Search] {get_failure_count()} consecutive "
                                f"fix failures detected. Use web_search to find solutions — "
                                f"search both English (StackOverflow, GitHub) and Chinese "
                                f"(CSDN, Zhihu, Juejin) sources. web_fetch the top results, "
                                f"extract actionable fix steps, and present a Fix Plan to "
                                f"the user before applying any changes."
                            )
                        })
            finally:
                set_state(SpinnerState.IDLE)

            if tool_name == "todo_write":
                rounds_since_todo=0
            else:
                rounds_since_todo+=1

            # Strip ANSI codes from LLM-bound messages (keep for user display)
            import re as _re
            _ansi_re = _re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
            clean_output = _ansi_re.sub('', str(output))
            tool_results_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "name": tool_name,
                            "content": clean_output
                        })


        if compacted_now:
            messages.extend(tool_results_messages)
            continue
        messages.extend(tool_results_messages)



def print_turn_assistants(messages: list, turn_start: int):
    """Render assistant responses from the current turn using Rich Markdown."""
    for msg in messages[turn_start:]:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if content:
            render_assistant(content)
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

def main(argv: list[str] | None = None):
    """Main entry point — called from CLI or programmatic use."""

    # ── Parse arguments FIRST (before any setup that depends on workdir) ──
    import argparse
    ap = argparse.ArgumentParser(description="cc_mine - a Claude Code clone")
    ap.add_argument("--workdir", default=None, help="Working directory")
    ap.add_argument("--model", default=None, help="Override PRIMARY_MODEL")
    ap.add_argument("--resume", default=None, help="Resume a saved session by ID")
    ap.add_argument("--session-label", default="", help="Label for auto-saved session")
    ap.add_argument("--yes", "-y", default=False, action="store_true", help="Skip prompts, auto-resume latest session")
    ap.add_argument("--new", default=False, action="store_true", help="Start fresh, skip session resume")
    args = ap.parse_args(argv)

    if args.workdir:
        config.WORKDIR = Path(args.workdir).resolve()
    if args.model:
        import os
        os.environ["PRIMARY_MODEL"] = args.model

    # ── Setup after arg parsing ──
    from log_setup import setup_logging, get_logger
    setup_logging(config.WORKDIR)
    log = get_logger()
    log.info(f"=== cc_mine starting | workdir={config.WORKDIR} ===")

    # Create all runtime directories once before anything else
    config.ensure_directories()

    # Clear stale session/output data from previous runs
    import shutil
    for _d in [config.TRANSCRIPT_DIR, config.TOOL_RESULTS_DIR,
               config.SESSIONS_DIR, config.TASK_OUTPUTS_DIR]:
        if _d.exists():
            shutil.rmtree(_d, ignore_errors=True)
            _d.mkdir(parents=True, exist_ok=True)

    from planning import init_planning
    init_planning(config.WORKDIR)

    # ── Session resume ──
    from session import load_session, latest_session, save_session, get_last_crash, clear_crash_flag
    session_id = args.resume

    # Priority 0: --new flag skips all resume
    if args.new:
        session_id = None

    # Priority 1: detect crashed session → auto-prompt
    if session_id is None and not args.new:
        crashed_sid = get_last_crash(config.WORKDIR)
        if crashed_sid:
            print(f"\n  \033[31m[!] Detected crashed session: {crashed_sid}\033[0m")
            print(f"  The previous run was interrupted by an error. Your task progress was saved.")
            if args.yes:
                session_id = crashed_sid
                clear_crash_flag(config.WORKDIR)
            else:
                choice = input(f"  Resume crashed session? [Y/n] ").strip().lower()
                if choice in ("", "y", "yes"):
                    session_id = crashed_sid
                    clear_crash_flag(config.WORKDIR)
                else:
                    print(f"  Starting fresh. Crashed session kept for later recovery.\n")

    # Priority 2: check for latest session
    if session_id is None and not args.new:
        latest = latest_session(config.WORKDIR)
        if latest:
            if args.yes:
                session_id = latest
            else:
                print(f"Latest session: {latest}")
                choice = input("Resume? [Y/n] ").strip().lower()
                if choice in ("", "y", "yes"):
                    session_id = latest

    #运行主函数
    config.CLI_ACTIVE = True
    print(f"cc_mine agent  |  workdir: {config.WORKDIR}\n")
    print("Esc+Enter to send  |  Ctrl+D to exit  |  /help for commands\n")
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

    # Start spinner for visual feedback during LLM calls and tool execution
    from spinner import start_spinner
    start_spinner()

    # Initialize agent mode (auto/ask) from environment
    from mode_manager import init_mode
    init_mode()

    # Auto-save session ID
    _auto_session_id = session_id or f"session_{int(time.time())}"

    # ── Import the new prompt_toolkit input, with fallback ──
    try:
        from prompt_toolkit_input import read_input
    except ImportError:
        # Fallback: old input()-based multi-line reader
        def read_input() -> str | None:
            """Legacy multi-line input: double-Enter to submit."""
            from repl_ui import render_header
            lines = []
            first = True
            while True:
                try:
                    if first:
                        render_header()
                    prompt = "cc_mine > " if first else "...   "
                    line = input(prompt)
                except (EOFError, KeyboardInterrupt):
                    return None
                if first and line.strip().lower() in ("q", "exit"):
                    return None
                if first and line.strip() == "":
                    return None
                if not first and line.strip() == "":
                    break
                if first and line.strip():
                    lines.append(line.strip())
                    first = False
                elif not first:
                    lines.append(line)
            return "\n".join(lines) if lines else None

    while True:
        # Keep CLI commands in sync with current state
        cli_commands.set_shared_state(history, context, config.WORKDIR, _auto_session_id)

        query = read_input()
        if query is None:
            break
        if query.strip() == "":
            continue

        # ── Slash command dispatch ──
        if query.strip().startswith("/"):
            response, should_exit = cli_commands.handle_cli_command(query)
            if response:
                print(response)
            if should_exit:
                break
            continue

        trigger_hooks("UserPromptSubmit", query)
        #检查输入的prompt
        turn_start = len(history)

        # ── Multimodal: merge pending attachments into user message ──
        if has_pending():
            blocks = drain_pending()
            blocks.append({"type": "text", "text": query})
            history.append({"role": "user", "content": blocks})
            n_att = len(blocks) - 1  # minus the text block just added
            print(f"  \033[35m[multimodal] sending message with {n_att} attachment(s)\033[0m")
        else:
            history.append({"role": "user", "content": query})
        from output_manager import clear_outputs
        clear_outputs()
        try:
            # ── Start TUI for full-screen agent output ──
            _tui = None
            try:
                from tui import AgentTUI
                _tui = AgentTUI()
                _tui.start()
            except Exception:
                pass  # Fall back to inline rendering if TUI unavailable

            try:
                with agent_lock:
                    agent_loop(history, context)
                    context = update_context(context, history)
                # Agent finished normally — render assistant response
                print_turn_assistants(history, turn_start)
                # Auto-save after each completed turn (and clear crash flag)
                save_session(history, context, config.WORKDIR, _auto_session_id,
                             args.session_label)
                clear_crash_flag(config.WORKDIR)  # successful turn = no longer crashed
            finally:
                if _tui:
                    _tui.stop()
        except KeyboardInterrupt:
            # TUI already stopped by the finally above if we were in agent_loop
            print(f"\n  \033[33m[interrupted] returning to REPL...\033[0m")
            try:
                save_session(history, context, config.WORKDIR, _auto_session_id,
                             f"{args.session_label}-interrupted", crashed=True)
            except Exception:
                pass
            print()

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


if __name__ == "__main__":
    main()