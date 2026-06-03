"""
对比测试：旧 vs 新压缩管道的信息保留率

模拟一个典型 bug-fix 流程，对比：
- OLD: 直接丢弃 + "Re-run command if needed"
- NEW: AI摘要 + 保留关键信息
"""
import json
import sys
from pathlib import Path
import copy

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory import (
    _summarize_section, _tool_result_digest,
    tool_result_budget, snip_compact, micro_compact,
    estimate_tokens, estimate_size, collect_tool_results,
    KEEP_RECENT_TOOL_RESULTS
)
from config import CONTEXT_LIMIT


def simulate_session(turns: int = 25) -> list[dict]:
    """生成典型的 bug-fix 对话"""
    messages = []
    messages.append({"role": "system", "content": "You are a coding agent."})

    files = ["src/payment.py", "src/discount.py", "src/invoice.py",
             "tests/test_payment.py", "config/settings.py"]

    for turn in range(turns):
        if turn == 0:
            msg = "Bug: payment calculation wrong for orders over $100. Find and fix."
        elif turn == turns - 1:
            msg = "Fix confirmed. Add a unit test for the edge case."
        else:
            continue_msgs = [
                "ok continue", "what did you find?",
                "keep going", "run the tests now",
            ]
            msg = continue_msgs[turn % len(continue_msgs)]

        messages.append({"role": "user", "content": msg})

        # Simulate different phases
        if turn < turns * 0.4:
            # Exploration phase: read files
            fname = files[turn % len(files)]
            tool_calls = [{"id": f"tc_{turn}", "type": "function",
                          "function": {"name": "read_file", "arguments": json.dumps({"path": fname})}}]
            messages.append({"role": "assistant", "content": f"Reading {fname} to understand the code...",
                           "tool_calls": tool_calls})
            # Simulated file content
            result = (f"# {fname}\n" +
                     "class PaymentCalculator:\n" +
                     "    def calculate(self, amount, discount=0):\n" +
                     "        return amount * (1 - discount)\n" * 30)[:2000]
            messages.append({"role": "tool", "tool_call_id": f"tc_{turn}",
                           "name": "read_file", "content": result})
        elif turn < turns * 0.7:
            # Debug phase: run tests
            tool_calls = [{"id": f"tc_{turn}", "type": "function",
                          "function": {"name": "bash", "arguments": json.dumps({"command": "pytest tests/test_payment.py -v"})}}]
            messages.append({"role": "assistant", "content": "Running tests to see the failure...",
                           "tool_calls": tool_calls})
            result = ("=============================\n" +
                     "FAILED tests/test_payment.py::test_large_order - AssertionError: expected 90.0, got 100.0\n" +
                     "1 passed, 1 failed\n" * 20)[:1500]
            messages.append({"role": "tool", "tool_call_id": f"tc_{turn}",
                           "name": "bash", "content": result})
        else:
            # Fix phase: edit files
            tool_calls = [{"id": f"tc_{turn}", "type": "function",
                          "function": {"name": "edit_file", "arguments": json.dumps({"path": "src/payment.py", "old_text": "discount=0", "new_text": "discount=0.1"})}}]
            messages.append({"role": "assistant", "content": "Found the bug — discount not applied. Fixing...",
                           "tool_calls": tool_calls})
            messages.append({"role": "tool", "tool_call_id": f"tc_{turn}",
                           "name": "edit_file", "content": "File edited successfully."})

    return messages


def run_old_pipeline(messages):
    """模拟旧版压缩管道"""
    msgs = copy.deepcopy(messages)

    # Layer 1: tool_result_budget (unchanged)
    msgs = tool_result_budget(msgs)

    # Layer 2: OLD snip_compact — just cuts
    if len(msgs) > 50:
        keep_head, keep_tail = 3, 47
        snipped = len(msgs) - keep_head - keep_tail
        msgs = (msgs[:keep_head] +
                [{"role": "user", "content": f"[System Note: Snipped {snipped} historical messages to save memory.]"}] +
                msgs[-keep_tail:])

    # Layer 3: OLD micro_compact — "Re-run command if needed"
    tool_results = [(i, m) for i, m in enumerate(msgs) if m.get("role") == "tool"]
    if len(tool_results) > KEEP_RECENT_TOOL_RESULTS:
        for _, msg in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
            if len(str(msg.get("content", ""))) > 120:
                msg["content"] = "[Earlier tool result compacted by system, Re-run command if needed.]"

    return msgs


def run_new_pipeline(messages):
    """模拟新版压缩管道"""
    msgs = copy.deepcopy(messages)

    # Layer 1: unchanged
    msgs = tool_result_budget(msgs)

    # Layer 2: NEW snip_compact — AI summarizes middle
    if len(msgs) > 50:
        keep_head, keep_tail = 3, 47
        middle = msgs[keep_head:len(msgs) - keep_tail]
        summary = _summarize_section(middle)
        msgs = (msgs[:keep_head] +
                [{"role": "user", "content": f"[Context: earlier conversation summarized]\n\n{summary}"}] +
                msgs[-keep_tail:])

    # Layer 3: NEW micro_compact — keep digest
    tool_results = [(i, m) for i, m in enumerate(msgs) if m.get("role") == "tool"]
    if len(tool_results) > KEEP_RECENT_TOOL_RESULTS:
        for _, msg in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
            if len(str(msg.get("content", ""))) > 120:
                msg["content"] = _tool_result_digest(msg)

    return msgs


def analyze_quality(messages, label):
    """分析压缩后的消息质量"""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    total_tok = estimate_tokens(messages)
    print(f"  Total tokens: ~{total_tok:,}")

    # 统计各类消息
    roles = {}
    for m in messages:
        roles[m.get("role", "?")] = roles.get(m.get("role", "?"), 0) + 1
    print(f"  Messages: {roles}")

    # 检查是否有 "Re-run" 提示 (有害)
    rerun_count = sum(1 for m in messages
                     if "Re-run command if needed" in str(m.get("content", "")))
    # 检查是否有 AI 摘要 (有益)
    summary_count = sum(1 for m in messages
                       if "earlier conversation summarized" in str(m.get("content", "")).lower())
    # 检查压缩摘要质量
    digest_count = sum(1 for m in messages
                      if "Compacted" in str(m.get("content", "")) and "result" in str(m.get("content", "")))

    print(f"  'Re-run' prompts (有害): {rerun_count}")
    print(f"  AI summaries injected:  {summary_count}")
    print(f"  Tool result digests:    {digest_count}")

    # 看看压缩后的 tool 内容是否还有用
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    useful_tools = 0
    useless_tools = 0
    for m in tool_msgs:
        content = str(m.get("content", ""))
        if "Re-run command if needed" in content:
            useless_tools += 1
        elif "Compacted" in content and "Key info preserved" in content:
            useful_tools += 1  # 压缩但保留了信息
        elif len(content) > 100:
            useful_tools += 1  # 完整保留
        else:
            useless_tools += 1

    print(f"  Tool results: {useful_tools} useful, {useless_tools} useless (of {len(tool_msgs)} total)")

    return {
        "tokens": total_tok,
        "rerun_prompts": rerun_count,
        "summaries": summary_count,
        "useful_tools": useful_tools,
        "useless_tools": useless_tools,
    }


if __name__ == "__main__":
    print("=" * 60)
    print("  压缩管道: OLD vs NEW 质量对比")
    print("=" * 60)

    messages = simulate_session(turns=25)
    print(f"\n原始对话: {len(messages)} 条消息, ~{estimate_tokens(messages):,} tokens")

    old_result = run_old_pipeline(messages)
    new_result = run_new_pipeline(messages)

    old_quality = analyze_quality(old_result, "OLD (丢弃 + Re-run)")
    new_quality = analyze_quality(new_result, "NEW (摘要 + 保留)")

    print(f"\n\n{'='*60}")
    print(f"  对比总结")
    print(f"{'='*60}")
    print(f"  {'指标':<30} {'OLD':>12} {'NEW':>12}")
    print(f"  {'-'*54}")
    print(f"  {'Token 消耗':<30} {old_quality['tokens']:>12,} {new_quality['tokens']:>12,}")
    print(f"  {'有害 Re-run 提示':<30} {old_quality['rerun_prompts']:>12} {new_quality['rerun_prompts']:>12}")
    print(f"  {'AI 摘要注入':<30} {old_quality['summaries']:>12} {new_quality['summaries']:>12}")
    print(f"  {'有用 tool 结果':<30} {old_quality['useful_tools']:>12} {new_quality['useful_tools']:>12}")
    print(f"  {'无用 tool 结果':<30} {old_quality['useless_tools']:>12} {new_quality['useless_tools']:>12}")

    # 预估重做成本
    old_redo = old_quality['rerun_prompts'] * 2500  # 每个 re-run 约2500 tokens
    new_redo = new_quality['rerun_prompts'] * 2500
    print(f"\n  {'预估重做成本':<30} {old_redo:>12,} {new_redo:>12,}")
    print(f"  {'净效果':<30} {'亏损 :(' if old_redo > 0 else '持平':>12} {'盈利 :)' if new_redo < old_redo else '持平':>12}")

    print(f"\n结论:")
    if new_quality['rerun_prompts'] == 0 and old_quality['rerun_prompts'] > 0:
        print(f"  [OK] NEW 完全消除了有害的 Re-run 提示!")
    if new_quality['summaries'] > old_quality['summaries']:
        print(f"  [OK] NEW 用 AI 摘要替代了直接丢弃")
    if new_quality['useful_tools'] > old_quality['useful_tools']:
        print(f"  [OK] NEW 保留了更多可用的工具结果")
