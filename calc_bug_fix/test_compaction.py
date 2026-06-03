"""
模拟测试：量化四层上下文压缩对信息保留的影响

模拟场景：一个典型的 bug 修复流程
1. 用户报告 bug
2. Agent 读取多个文件探索代码
3. 运行测试确认问题
4. 修改文件修复
5. 运行测试验证

追踪每一层压缩后的信息保留率
"""
import json
import random
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory import (
    tool_result_budget, snip_compact, micro_compact,
    compact_history, estimate_tokens, estimate_size,
    prepare_context, KEEP_RECENT_TOOL_RESULTS
)
from config import CONTEXT_LIMIT


def simulate_session(turns: int = 20) -> list[dict]:
    """生成一个典型的 agent 对话"""
    messages = []

    # System prompt
    messages.append({"role": "system", "content": "You are a coding agent..." * 50})

    for turn in range(turns):
        # User message
        if turn == 0:
            msg = "There's a bug in the payment calculation. Please find and fix it."
        elif turn == turns - 1:
            msg = "Great, the fix works. Can you also add a unit test?"
        else:
            msg = "ok continue"

        messages.append({"role": "user", "content": msg})

        # Assistant with tool calls
        tool_calls = []
        if turn < turns // 2:
            # First half: exploration
            tool_calls = [
                {"id": f"tc_{turn}_0", "type": "function",
                 "function": {"name": "read_file", "arguments": json.dumps({"path": f"src/module_{turn % 5}.py"})}},
            ]
        elif turn < turns - 2:
            # Middle: editing
            tool_calls = [
                {"id": f"tc_{turn}_0", "type": "function",
                 "function": {"name": "edit_file", "arguments": json.dumps({"path": "src/payment.py", "old_text": "...", "new_text": "..."})}},
            ]
        else:
            # End: testing
            tool_calls = [
                {"id": f"tc_{turn}_0", "type": "function",
                 "function": {"name": "bash", "arguments": json.dumps({"command": "pytest tests/test_payment.py -v"})}},
            ]

        messages.append({
            "role": "assistant",
            "content": f"Let me work on this. Turn {turn}",
            "tool_calls": tool_calls
        })

        # Tool result (simulating read_file output with varying sizes)
        if turn < turns // 2:
            # read_file results: ~1500 chars of code
            result = f"# File: src/module_{turn % 5}.py\n" + ("def foo():\n    pass\n" * 100)[:1500]
        elif turn < turns - 2:
            result = "File edited successfully."
        else:
            # Test output: can be large
            result = "tests/test_payment.py::test_calculate PASSED\n" + ("test_other.py::test_foo PASSED\n" * 50)[:3000]

        messages.append({
            "role": "tool",
            "tool_call_id": f"tc_{turn}_0",
            "name": tool_calls[0]["function"]["name"],
            "content": result
        })

    return messages


def analyze_pipeline(messages: list[dict], label: str):
    """分析压缩管道各层的影响"""
    import copy
    original = copy.deepcopy(messages)

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  初始状态: {len(messages)} 条消息, ~{estimate_tokens(messages):,} tokens, {estimate_size(messages)//1024}KB")

    # Layer 1: tool_result_budget
    msgs = copy.deepcopy(original)
    msgs = tool_result_budget(msgs)
    layer1_tok = estimate_tokens(msgs)
    layer1_msgs = len(msgs)
    tool_results_before = sum(1 for m in original if m.get("role") == "tool")
    tool_results_after = sum(1 for m in msgs if m.get("role") == "tool")
    # 检查内容被截断的 tool results
    truncated_in_layer1 = sum(
        1 for m in msgs if m.get("role") == "tool"
        and "<persisted-output>" in str(m.get("content", ""))
    )
    print(f"\n  [Layer 1] tool_result_budget (max 200KB):")
    print(f"    消息数: {layer1_msgs} (不变)")
    print(f"    Token: ~{layer1_tok:,}")
    print(f"    Tool结果被截断到磁盘: {truncated_in_layer1} / {tool_results_after}")
    print(f"    → 影响: {'无触发' if truncated_in_layer1 == 0 else f'{truncated_in_layer1}个结果只剩2KB预览，LLM需重新读取' if truncated_in_layer1 > 0 else '无'}")

    # Layer 2: snip_compact
    msgs2 = copy.deepcopy(original)
    msgs2 = tool_result_budget(msgs2)
    before_snip = len(msgs2)
    msgs2 = snip_compact(msgs2)
    layer2_tok = estimate_tokens(msgs2)
    layer2_msgs = len(msgs2)
    snipped_count = before_snip - layer2_msgs - 1  # minus snip note
    # 分析被 snip 的消息类型
    if snipped_count > 0:
        # 模拟：哪些消息被剪掉了
        keep_head, keep_tail = 3, 47  # max_messages=50
        snipped_section = original[keep_head:len(original)-keep_tail]
        snipped_tools = sum(1 for m in snipped_section if m.get("role") == "tool")
        snipped_assistants = sum(1 for m in snipped_section if m.get("role") == "assistant")
        snipped_users = sum(1 for m in snipped_section if m.get("role") == "user")
    else:
        snipped_tools = snipped_assistants = snipped_users = 0

    print(f"\n  [Layer 2] snip_compact (max 50 msgs):")
    print(f"    消息数: {before_snip} → {layer2_msgs} (剪掉 {snipped_count} 条中段消息)")
    print(f"    Token: ~{layer2_tok:,}")
    if snipped_count > 0:
        print(f"    被剪掉的内容: {snipped_tools} tool结果, {snipped_assistants} assistant回复, {snipped_users} user消息")
    print(f"    → 影响: {'无触发' if snipped_count <= 0 else f'丢失{snipped_count}条上下文，中间的探索/分析全部消失' if snipped_count > 10 else f'轻微'}")

    # Layer 3: micro_compact
    msgs3 = copy.deepcopy(original)
    msgs3 = tool_result_budget(msgs3)
    msgs3 = snip_compact(msgs3)
    before_micro = estimate_tokens(msgs3)
    msgs3 = micro_compact(msgs3)
    layer3_tok = estimate_tokens(msgs3)
    frozen_count = sum(
        1 for m in msgs3 if m.get("role") == "tool"
        and "compacted by system" in str(m.get("content", ""))
    )
    print(f"\n  [Layer 3] micro_compact (保留最近 {KEEP_RECENT_TOOL_RESULTS} 个结果):")
    print(f"    Token: ~{before_micro:,} → ~{layer3_tok:,}")
    print(f"    工具结果被冻结: {frozen_count} 个")
    print(f"    → 影响: {'无触发' if frozen_count == 0 else f'{frozen_count}个结果被替换为"Re-run command if needed"提示 → 直接引导LLM重跑！'}")

    # Layer 4: compact_history (AI摘要)
    msgs4 = copy.deepcopy(original)
    msgs4 = tool_result_budget(msgs4)
    msgs4 = snip_compact(msgs4)
    msgs4 = micro_compact(msgs4)
    if estimate_size(msgs4) > CONTEXT_LIMIT:
        would_trigger = True
        # 模拟: 不实际调用LLM，用占位符
        print(f"\n  [Layer 4] compact_history (AI摘要坍缩):")
        print(f"    [*] 触发! 上下文超 {CONTEXT_LIMIT} tokens")
        print(f"    → 影响: 用flash小模型摘要 + 最后5条替换全部历史")
        print(f"    → 风险: 小模型遗漏关键信息，摘要质量不可控")
    else:
        would_trigger = False
        print(f"\n  [Layer 4] compact_history:")
        print(f"    未触发 (上下文 {estimate_size(msgs4)//1024}KB < {CONTEXT_LIMIT} limit)")

    # 综合评估
    total_original_tok = estimate_tokens(original)
    total_final_tok = estimate_tokens(msgs3)
    retention = (total_final_tok / total_original_tok * 100) if total_original_tok > 0 else 100

    print(f"\n  {'─'*50}")
    print(f"  综合: 原始 ~{total_original_tok:,} tok → 压缩后 ~{total_final_tok:,} tok")
    print(f"  信息保留率: {retention:.0f}% (但这不等于可用信息比例)")

    # Re-work 预测
    frozen_tool_types = []
    for m in msgs3:
        if m.get("role") == "tool" and "compacted by system" in str(m.get("content", "")):
            frozen_tool_types.append(m.get("name", "?"))

    rework_estimate = 0
    for t in frozen_tool_types:
        if t == "read_file":
            rework_estimate += 2500  # read_file call + result tokens
        elif t == "bash":
            rework_estimate += 3500  # bash call + output tokens

    print(f"\n  [*] 预估重做token浪费: ~{rework_estimate:,} tokens")
    print(f"    (如果LLM需要重新获取被冻结的信息)")

    return {
        "original_tokens": total_original_tok,
        "final_tokens": total_final_tok,
        "retention_pct": retention,
        "frozen_count": frozen_count,
        "snipped_count": snipped_count,
        "rework_estimate": rework_estimate,
        "would_compact": would_trigger,
    }


if __name__ == "__main__":
    print("=" * 60)
    print("  上下文压缩管道分析 — 信息损失量化")
    print("=" * 60)
    print(f"  配置: CONTEXT_LIMIT={CONTEXT_LIMIT}, KEEP_RECENT_TOOL_RESULTS={KEEP_RECENT_TOOL_RESULTS}")
    print(f"  snip_compact max_messages=50, tool_result_budget max_bytes=200KB")

    # 场景1：短对话（不会触发压缩）
    print("\n\n┌─────────────────────────────────────────────────────┐")
    print("│  场景1: 短对话 (10轮，约30条消息)                   │")
    print("└─────────────────────────────────────────────────────┘")
    s1 = simulate_session(turns=10)
    r1 = analyze_pipeline(s1, "场景1: 短对话")

    # 场景2：中等对话（触发 snip + micro）
    print("\n\n┌─────────────────────────────────────────────────────┐")
    print("│  场景2: 中等对话 (25轮，约76条消息)                 │")
    print("└─────────────────────────────────────────────────────┘")
    s2 = simulate_session(turns=25)
    r2 = analyze_pipeline(s2, "场景2: 中等对话")

    # 场景3：长对话（全部触发）
    print("\n\n┌─────────────────────────────────────────────────────┐")
    print("│  场景3: 长对话 (40轮，约121条消息)                  │")
    print("└─────────────────────────────────────────────────────┘")
    s3 = simulate_session(turns=40)
    # 人工膨胀消息，让它超 CONTEXT_LIMIT
    for m in s3:
        if m.get("role") == "tool":
            m["content"] = m["content"] * 5  # 扩大工具输出
    r3 = analyze_pipeline(s3, "场景3: 长对话(膨胀版)")

    # 汇总
    print("\n\n" + "=" * 60)
    print("  三层场景对比")
    print("=" * 60)
    print(f"  {'场景':<20} {'原始tok':>10} {'压缩tok':>10} {'保留率':>8} {'冻结':>6} {'剪除':>6} {'预估重做':>10} {'摘要':>6}")
    print(f"  {'-'*60}")
    for name, r in [("短对话(10轮)", r1), ("中等对话(25轮)", r2), ("长对话(40轮)", r3)]:
        print(f"  {name:<20} {r['original_tokens']:>10,} {r['final_tokens']:>10,} "
              f"{r['retention_pct']:>7.0f}% {r['frozen_count']:>5} {r['snipped_count']:>5} "
              f"{r['rework_estimate']:>10,} {'[*]' if r['would_compact'] else '[OK]':>6}")
