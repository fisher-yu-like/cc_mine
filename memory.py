import AutonomousAgent
import mcp
from bg_task import collect_background_results
from config import MEMORY_DIR, MEMORY_INDEX, PERSIST_THRESHOLD, KEEP_RECENT_TOOL_RESULTS, TOOL_RESULTS_DIR, \
    TRANSCRIPT_DIR, CONTEXT_LIMIT
from call_llm import client, estimate_tokens
import json
import time
from pathlib import Path


# ── 更新上下文 ──
def update_context(context: dict, messages: list) -> dict:
    parts = []

    # Legacy MEMORY.md
    if MEMORY_INDEX.exists():
        try:
            parts.append(MEMORY_INDEX.read_text(encoding="utf-8")[:2000])
        except UnicodeDecodeError:
            parts.append(MEMORY_INDEX.read_text(encoding="gbk", errors="replace")[:2000])

    # New: scan memory/ directory for .md files with frontmatter
    mem_dir = MEMORY_DIR
    if mem_dir.exists():
        from skill_load import _parse_frontmatter
        for mf in sorted(mem_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            if mf.name == "MEMORY.md":
                continue  # already handled above
            try:
                raw = mf.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raw = mf.read_text(encoding="gbk", errors="replace")
            meta, body = _parse_frontmatter(raw)
            title = meta.get("title", mf.stem)
            parts.append(f"[{title}]\n{body[:500]}")

    memories = "\n\n".join(parts)[:3000]
    return {
        "memories": memories,
        "connected_mcp": list(mcp.mcp_clients.keys()),
        "active_teammates": list(AutonomousAgent.active_teammates.keys()),
    }


# ── Structured memory CRUD ──
def add_memory(title: str, content: str, tags: str = "") -> str:
    """Create a new memory card in the memory directory."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    import re
    slug = re.sub(r'[^a-z0-9_-]', '-', title.lower())[:40]
    path = MEMORY_DIR / f"{slug}.md"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    tags_yaml = f"[{', '.join(t.strip() for t in tags.split(',') if t.strip())}]" if tags else "[]"
    body = (
        f"---\n"
        f"title: \"{title}\"\n"
        f"tags: {tags_yaml}\n"
        f"created: {ts}\n"
        f"updated: {ts}\n"
        f"---\n"
        f"\n{content}\n"
    )
    path.write_text(body, encoding="utf-8")
    print(f"  \033[32m[memory] +{slug}\033[0m")
    return f"Memory '{title}' saved as {slug}"


def search_memory(query: str) -> str:
    """Full-text search across memory files."""
    if not MEMORY_DIR.exists():
        return "(no memories yet)"
    results = []
    qlower = query.lower()
    for mf in sorted(MEMORY_DIR.glob("*.md")):
        try:
            text = mf.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            text = mf.read_text(encoding="gbk", errors="replace").lower()
        if qlower in text:
            results.append(f"- `{mf.stem}`: {text[:120].strip()}")
    if not results:
        return f"(no matches for '{query}')"
    return "\n".join(results[:10])


def delete_memory(name: str) -> str:
    """Delete a memory card by slug name."""
    path = MEMORY_DIR / f"{name}.md"
    if path.exists():
        path.unlink()
        print(f"  \033[31m[memory] -{name}\033[0m")
        return f"Deleted memory '{name}'"
    return f"Memory '{name}' not found"


# ── 孤儿消息清理 ──
def _strip_orphan_tools(messages: list) -> list:
    """Remove orphan pairings in both directions broken during compaction.
    (1) tool messages without a prior assistant tool_calls → removed
    (2) assistant tool_calls without any responding tool message → stripped"""
    declared_ids = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in (msg.get("tool_calls") or []):
                declared_ids.add(tc.get("id", ""))
    responded_ids = set()
    for msg in messages:
        if msg.get("role") == "tool":
            responded_ids.add(msg.get("tool_call_id", ""))

    stripped_calls = 0
    removed_tools = 0
    cleaned = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            live_calls = [tc for tc in msg["tool_calls"] if tc.get("id", "") in responded_ids]
            removed = len(msg["tool_calls"]) - len(live_calls)
            stripped_calls += removed
            msg = dict(msg)
            if live_calls:
                msg["tool_calls"] = live_calls
            else:
                msg.pop("tool_calls", None)
            cleaned.append(msg)
        elif msg.get("role") == "tool":
            if msg.get("tool_call_id", "") not in declared_ids:
                removed_tools += 1
                continue
            cleaned.append(msg)
        else:
            cleaned.append(msg)
    if stripped_calls or removed_tools:
        print(f"  \033[90m[orphan clean] {stripped_calls} orphan tool_calls stripped, {removed_tools} orphan tool msgs removed\033[0m")
    return cleaned


# ── 上下文准备 ──
def prepare_context(messages: list) -> list:
    before = len(messages)
    before_bytes = estimate_size(messages)
    messages[:] = tool_result_budget(messages)
    messages[:] = snip_compact(messages)
    messages[:] = micro_compact(messages)
    if estimate_size(messages) > CONTEXT_LIMIT:
        messages[:] = compact_history(messages)
    messages[:] = _strip_orphan_tools(messages)
    after = len(messages)
    after_bytes = estimate_size(messages)
    if before != after or before_bytes > after_bytes:
        before_tok = estimate_tokens(messages[:before]) if before else 0
        after_tok = estimate_tokens(messages) if after else 0
        print(f"  \033[90m[context] {before}→{after} msgs, ~{before_tok}→~{after_tok} tok, {before_bytes//1024}KB→{after_bytes//1024}KB\033[0m")
    return messages


def build_user_content(results: list[dict]) -> list[dict]:
    content = list(results)
    for note in collect_background_results():
        content.append({"type": "text", "text": note})
    return content


def inject_background_notifications(messages: list):
    from subagent import collect_subagent_results

    notes = collect_background_results()
    if notes:
        messages.append({"role": "user", "content": [
            {"type": "text", "text": note} for note in notes]})
    # Inject async subagent results
    sub_results = collect_subagent_results()
    if sub_results:
        messages.extend(sub_results)


# ═══════════════════════════════════════════════════════════
# 上下文自适应裁剪与记忆管理系统
# ═══════════════════════════════════════════════════════════

def estimate_size(messages: list) -> int:
    return len(json.dumps(messages, default=str))


def collect_tool_results(messages: list):
    found = []
    for mi, msg in enumerate(messages):
        if msg.get("role") == "tool":
            found.append((mi, msg))
    return found


def persist_large_output(tool_call_id: str, output: str) -> str:
    if len(output) <= PERSIST_THRESHOLD:
        return output
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOL_RESULTS_DIR / f"{tool_call_id}.txt"
    if not path.exists():
        path.write_text(output, encoding="utf-8")
    return (f"<persisted-output>\nFull output cached at: {path}\n"
            f"Preview:\n{output[:2000]}\n</persisted-output>")


# ── 第一道防线：大文本抽离 ──
def tool_result_budget(messages: list, max_bytes: int = 200000) -> list:
    if not messages:
        return messages
    tool_msg = [m for m in messages if m.get("role") == "tool"]
    total = sum(len(str(m.get("content", ""))) for m in tool_msg)
    if total <= max_bytes:
        return messages
    persisted_count = 0
    saved_bytes = 0
    for msg in sorted(tool_msg, key=lambda m: len(str(m.get("content", ""))), reverse=True):
        if total <= max_bytes:
            break
        before = len(str(msg.get("content", "")))
        text = str(msg.get("content", ""))
        msg["content"] = persist_large_output(msg.get("tool_call_id", "unknown"), text)
        after = len(str(msg.get("content", "")))
        saved_bytes += before - after
        persisted_count += 1
        total = sum(len(str(m.get("content", ""))) for m in tool_msg)
    if persisted_count:
        print(f"  \033[33m[budget compact] {persisted_count} tool output(s) offloaded to disk, saved ~{saved_bytes//1024}KB\033[0m")
    return messages


# ── 第二道防线：消息流腰斩 ──
def snip_compact(messages: list, max_messages: int = 50) -> list:
    if len(messages) <= max_messages:
        return messages
    keep_head, keep_tail = 3, max_messages - 3
    snipped = len(messages) - keep_head - keep_tail
    print(f"  \033[33m[snip compact] {snipped} middle messages removed ({len(messages)}→{max_messages}), kept head {keep_head} + tail {keep_tail}\033[0m")
    return (messages[:keep_head] +
            [{"role": "user", "content": f"[System Note: Snipped {snipped} historical messages to save memory.]"}] +
            messages[-keep_tail:])


# ── 第三道防线：旧工具结果冷冻 ──
def micro_compact(messages: list) -> list:
    tool_results = collect_tool_results(messages)
    if len(tool_results) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    frozen_count = 0
    for _, msg in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
        if len(str(msg.get("content", ""))) > 120:
            msg["content"] = "[Earlier tool result compacted by system, Re-run command if needed.]"
            frozen_count += 1
    if frozen_count:
        print(f"  \033[33m[micro compact] {frozen_count} old tool results frozen (keeping last {KEEP_RECENT_TOOL_RESULTS})\033[0m")
    return messages


# ── 第四道防线：AI 摘要坍缩 ──
def summarize_history(messages: list) -> str:
    conversation = json.dumps(messages, default=str)[:80000]
    prompt = ("Summarize this coding-agent conversation so work can continue. "
              "Preserve current goal, key findings, changed files, remaining work, "
              "and user constraints.\n\n" + conversation)
    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[{"role": "system", "content": "You are a senior tech lead tracking agent states."},
                  {"role": "user", "content": prompt}],
        max_completion_tokens=2000
    )
    return response.choices[0].message.content or "(empty summary)"


def compact_history(messages: list, label: str = "full compact") -> list:
    from log_setup import get_logger
    log = get_logger()

    path = write_transcript(messages)
    est = estimate_tokens(messages)
    log.info(f"[{label}] {len(messages)} msgs (~{est} tok) archived to {path.name}, summarizing...")
    print(f"  \033[33m[{label}] {len(messages)} msgs (~{est} tok) archived to {path.name}, "
          f"{'summarizing...' if label == 'full compact' else 'condensing...'}\033[0m")
    try:
        summary = summarize_history(messages)
    except Exception:
        summary = "Earlier conversation was trimmed after a prompt-too-long error."
    print(f"  \033[33m[{label}] condensed to 1 summary + last 5 messages (was {len(messages)} total)\033[0m")
    return [{"role": "user", "content": f"[Reactive compact]\n\n{summary}"},
            *messages[-5:]]


def reactive_compact(messages: list) -> list:
    return compact_history(messages, label="reactive compact")


_transcript_session: str | None = None


def set_transcript_session(session_id: str):
    """Tag transcript files with a session ID for organization."""
    global _transcript_session
    _transcript_session = session_id


def write_transcript(messages: list, name: str = "") -> Path:
    """Archive conversation to JSONL. Uses session subdir if available."""
    base = TRANSCRIPT_DIR
    if _transcript_session:
        base = base / _transcript_session
    base.mkdir(parents=True, exist_ok=True)
    if name:
        filename = f"{name}.jsonl"
    else:
        filename = f"transcript_{int(time.time())}.jsonl"
    path = base / filename
    with path.open("w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return path


def list_transcripts(session_id: str = "") -> str:
    """List saved transcripts."""
    base = TRANSCRIPT_DIR / session_id if session_id else TRANSCRIPT_DIR
    if not base.exists():
        return "(no transcripts)"
    files = sorted(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return "(no transcripts)"
    lines = []
    for f in files[:20]:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))
        size = f.stat().st_size
        lines.append(f"  [{ts}] {f.name} ({size//1024}KB)")
    return "\n".join(lines) if lines else "(no transcripts)"
