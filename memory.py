import AutonomousAgent
import mcp
from bg_task import collect_background_results
from config import MEMORY_DIR, MEMORY_INDEX, PERSIST_THRESHOLD, KEEP_RECENT_TOOL_RESULTS, TOOL_RESULTS_DIR, \
    TRANSCRIPT_DIR, CONTEXT_LIMIT, MAX_TURNS, USER_MEMORY_DIR, AGENT_MEMORY_DIR, SHARED_MEMORY_DIR
from call_llm import get_client as _get_client, estimate_tokens
import json
import time
from pathlib import Path


# ── 更新上下文 ──
def _collect_memories(directory, label: str, max_items: int = 5) -> list[str]:
    """Collect memory cards from a directory, sorted by recency."""
    if not directory.exists():
        return []
    from skill_load import _parse_frontmatter
    parts = []
    for mf in sorted(directory.glob("*.md"),
                     key=lambda p: p.stat().st_mtime, reverse=True)[:max_items]:
        if mf.name == "MEMORY.md":
            continue
        try:
            raw = mf.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = mf.read_text(encoding="gbk", errors="replace")
        meta, body = _parse_frontmatter(raw)
        title = meta.get("title", mf.stem)
        parts.append(f"[{label}: {title}]\n{body[:400]}")
    return parts


def update_context(context: dict, messages: list) -> dict:
    parts = []

    # User memories (habits, preferences) — always include, highest priority
    user_parts = _collect_memories(USER_MEMORY_DIR, "User Pref", max_items=10)
    if user_parts:
        parts.append("## User Memories (habits & preferences)\n" +
                     "\n".join(user_parts))

    # Shared memories (worktree-independent)
    shared_parts = _collect_memories(SHARED_MEMORY_DIR, "Shared", max_items=5)
    if shared_parts:
        parts.append("## Shared Context\n" + "\n".join(shared_parts))

    # Agent memories (recent decisions) — limited to most recent 3
    agent_parts = _collect_memories(AGENT_MEMORY_DIR, "Agent Note", max_items=3)
    if agent_parts:
        parts.append("## Recent Agent Notes\n" + "\n".join(agent_parts))

    # Legacy MEMORY.md (backward compatibility)
    if MEMORY_INDEX.exists():
        try:
            parts.append(MEMORY_INDEX.read_text(encoding="utf-8")[:1000])
        except UnicodeDecodeError:
            parts.append(MEMORY_INDEX.read_text(encoding="gbk", errors="replace")[:1000])

    memories = "\n\n".join(parts)[:3000]
    return {
        "memories": memories,
        "connected_mcp": list(mcp.mcp_clients.keys()),
        "active_teammates": list(AutonomousAgent.active_teammates.keys()),
    }


# ── Structured memory CRUD ──


def _is_duplicate(content: str, target_dir, threshold: float = 0.6) -> bool:
    """Check if very similar memory content already exists.

    Uses Jaccard similarity on word sets (body only, ignoring frontmatter).
    Returns True if a near-duplicate is found.
    """
    new_words = set(content.lower().split())
    if not new_words:
        return False
    from skill_load import _parse_frontmatter
    for mf in sorted(target_dir.glob("*.md")):
        try:
            raw = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        _, body = _parse_frontmatter(raw)
        existing_words = set(body.lower().split())
        if not existing_words:
            continue
        overlap = len(new_words & existing_words)
        union = len(new_words | existing_words)
        if union > 0 and overlap / union > threshold:
            return True
    return False


def add_memory(title: str, content: str, tags: str = "",
               source: str = "agent") -> str:
    """Create a memory card. source='user' stores permanently;
    source='agent' stores transient decision notes.

    DO NOT memorize code structure or file contents that can be re-read.
    Only store user habits, preferences, and non-derivable decisions.
    """
    # Pick target directory based on source
    if source == "user":
        target_dir = USER_MEMORY_DIR
    elif source == "shared":
        target_dir = SHARED_MEMORY_DIR
    else:
        target_dir = AGENT_MEMORY_DIR

    if not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)

    # Dedup: skip if very similar content already exists
    if _is_duplicate(content, target_dir):
        return (f"Memory skipped: similar content already exists "
                f"(use /memory-add to force)")

    import re
    slug = re.sub(r'[^a-z0-9_-]', '-', title.lower())[:40]
    path = target_dir / f"{slug}.md"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    tags_yaml = f"[{', '.join(t.strip() for t in tags.split(',') if t.strip())}]" if tags else "[]"
    body = (
        f"---\n"
        f"title: \"{title}\"\n"
        f"tags: {tags_yaml}\n"
        f"source: {source}\n"
        f"created: {ts}\n"
        f"updated: {ts}\n"
        f"---\n"
        f"\n{content}\n"
    )
    path.write_text(body, encoding="utf-8")
    print(f"  \033[32m[memory] +{source}/{slug}\033[0m")
    return f"Memory '{title}' saved as {slug} [{source}]"


def search_memory(query: str) -> str:
    """Full-text search across ALL memory files (root + subdirs)."""
    if not MEMORY_DIR.exists():
        return "(no memories yet)"
    results = []
    qlower = query.lower()

    # Scan root level + all subdirectories
    search_dirs = [MEMORY_DIR]
    for sd in [USER_MEMORY_DIR, AGENT_MEMORY_DIR, SHARED_MEMORY_DIR]:
        if sd.exists():
            search_dirs.append(sd)

    for search_dir in search_dirs:
        for mf in sorted(search_dir.glob("*.md")):
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
    """Delete a memory card by slug name. Searches root + subdirs."""
    # Try each directory
    for target_dir in [MEMORY_DIR, USER_MEMORY_DIR,
                        AGENT_MEMORY_DIR, SHARED_MEMORY_DIR]:
        path = target_dir / f"{name}.md"
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


def _find_last_user_idx(messages: list) -> int:
    """Return the index of the LAST user message in the list.

    Messages FROM this index onwards belong to the CURRENT turn and
    must NEVER be compacted. Only messages BEFORE this index (previous
    turns) are eligible for compaction.
    """
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            return i
    return 0


# ── 上下文准备 ──
def prepare_context(messages: list) -> list:
    """Compact OLD conversation turns, preserving the CURRENT turn intact.

    The current turn (everything from the last user message onwards)
    is NEVER touched — this ensures the agent always sees the user's
    latest request and all tool output / assistant responses from the
    current turn in full.
    """
    before = len(messages)
    before_bytes = estimate_size(messages)

    # ── Split: [old turns ...] [current turn: last user msg → end] ──
    split_at = _find_last_user_idx(messages)
    old = messages[:split_at]
    current = messages[split_at:]

    if not old:
        # Only one turn so far — nothing to compact
        messages[:] = _strip_orphan_tools(messages)
        return messages

    old_size = estimate_size(old)

    # Layer 1: offload large tool outputs to disk (always safe)
    old = tool_result_budget(old)

    # Layers 2-3: aggressive compression on old turns only
    if old_size >= 40000:
        old = snip_compact(old)
        old = micro_compact(old)

    # Layer 4: full AI summary of old turns
    old_size = estimate_size(old)
    if old_size > 60000 or len(old) > MAX_TURNS:
        old = compact_history(old, label="context compact")

    # ── Rejoin: compacted old + pristine current ──
    messages[:] = old + current
    messages[:] = _strip_orphan_tools(messages)

    after = len(messages)
    after_bytes = estimate_size(messages)

    if before != after or before_bytes != after_bytes:
        est = estimate_tokens(messages)
        print(f"  \033[90m[context] {before}→{after} msgs, ~{est} tok, "
              f"{before_bytes//1024}KB→{after_bytes//1024}KB "
              f"(current turn: {len(current)} msgs preserved)\033[0m")
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
    if not TOOL_RESULTS_DIR.exists():
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


# ── 中间段 AI 摘要 ──
def _get_summary_model() -> str:
    """Return the best model for summarization based on current provider."""
    try:
        from call_llm import get_provider_info
        info = get_provider_info()
        if info.get("provider") == "ollama":
            return info.get("model", "llama3")
    except ImportError:
        pass
    return "deepseek-v4-flash"


def _summarize_section(messages: list) -> str:
    """Fast AI summary of a conversation section. Uses flash model for low cost."""
    if len(messages) <= 5:
        return f"Snipped {len(messages)} system/transition messages."

    # Extract key info without dumping full JSON
    compact_lines = []
    for m in messages:
        role = m.get("role", "?")
        if role == "user":
            content = str(m.get("content", ""))[:300]
            compact_lines.append(f"[user]: {content}")
        elif role == "assistant":
            content = str(m.get("content", ""))[:200]
            tool_names = []
            for tc in (m.get("tool_calls") or []):
                tool_names.append(tc.get("function", {}).get("name", "?"))
            tools_str = f" [called: {', '.join(tool_names)}]" if tool_names else ""
            compact_lines.append(f"[assistant]{tools_str}: {content}")
        elif role == "tool":
            name = m.get("name", "tool")
            content = str(m.get("content", ""))[:300]
            compact_lines.append(f"[tool:{name}]: {content}")
    conversation = "\n".join(compact_lines)[:30000]

    prompt = (
        "Summarize this coding session segment. PRESERVE specific details — "
        "the summary replaces the full history and must allow work to continue "
        "without losing context.\n"
        "CRITICAL: Keep ALL file paths, function names, class names, and variable names exactly.\n"
        "Do NOT paraphrase code identifiers — copy them verbatim.\n\n"
        "List:\n"
        "1. Files read — full path + key findings (function names, bugs, patterns)\n"
        "2. Commands run — exact command + key results (exit code, output highlights)\n"
        "3. Code changes — exact file paths + what changed + why (before/after)\n"
        "4. Decisions & conclusions — what was decided, ruled out, or chosen and why\n"
        "5. Errors & fixes — what broke, why, and how it was resolved\n"
        "Be thorough on technical specifics. Omit only redundant chatter.\n\n"
        + conversation
    )
    try:
        response = _get_client().chat.completions.create(
            model=_get_summary_model(),
            messages=[{"role": "system", "content": "You are a technical note-taker. Be brief and factual. Output structured bullet points."},
                      {"role": "user", "content": prompt}],
            max_completion_tokens=800
        )
        return response.choices[0].message.content or "(summary unavailable)"
    except Exception:
        return f"Earlier conversation ({len(messages)} msgs) — key context may be lost."


# ── 第二道防线：消息流腰斩 ──
def snip_compact(messages: list, max_messages: int = 70) -> list:
    if len(messages) <= max_messages:
        return messages
    keep_head, keep_tail = 15, max_messages - 15
    snipped = len(messages) - keep_head - keep_tail
    middle = messages[keep_head:len(messages) - keep_tail]

    # AI-summarize the snipped section instead of discarding it
    print(f"  \033[33m[snip compact] {snipped} middle messages → summarizing... ({len(messages)}→{max_messages})\033[0m")
    summary = _summarize_section(middle)

    return (messages[:keep_head] +
            [{"role": "user", "content": f"[Context: earlier conversation summarized]\n\n{summary}"}] +
            messages[-keep_tail:])


# ── 工具结果摘要 ──
def _tool_result_digest(msg: dict) -> str:
    """Keep a useful summary of a tool result instead of throwing it away."""
    name = msg.get("name", "tool")
    content = str(msg.get("content", ""))
    content_len = len(content)

    if content_len <= 500:
        return content

    # Keep head + tail so the LLM still has context
    head = content[:400]
    tail = content[-200:] if content_len > 600 else ""
    separator = "\n... [snip] ...\n" if tail else ""

    return (
        f"[Compacted {name} result — was {content_len} chars. "
        f"DO NOT RE-RUN. This work is already done. The full result is archived.]\n"
        f"{head}{separator}{tail}"
    )


# ── 第三道防线：旧工具结果冷冻 ──
# Tool names whose results should NEVER be compressed — these contain
# subagent/task output that the lead agent needs in full to make decisions.
_PRESERVE_TOOLS = {"task", "spawn_subagent", "spawn_teammate", "submit_plan",
                   "enter_plan_mode", "exit_plan_mode", "update_plan_step"}


def micro_compact(messages: list) -> list:
    tool_results = collect_tool_results(messages)
    if len(tool_results) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    frozen_count = 0
    skipped_count = 0
    for _, msg in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
        tool_name = msg.get("name", "")
        if tool_name in _PRESERVE_TOOLS:
            skipped_count += 1
            continue  # Never compress subagent/task/plan results
        if len(str(msg.get("content", ""))) > 120:
            msg["content"] = _tool_result_digest(msg)
            frozen_count += 1
    if frozen_count or skipped_count:
        print(f"  \033[33m[micro compact] {frozen_count} tool results condensed"
              + (f", {skipped_count} important results preserved" if skipped_count else "")
              + f" (keeping last {KEEP_RECENT_TOOL_RESULTS} full)\033[0m")
    return messages


# ── 第四道防线：AI 摘要坍缩 ──
def summarize_history(messages: list) -> str:
    conversation = json.dumps(messages, default=str)[:80000]
    prompt = (
        "You are summarizing a coding-agent session so work can continue seamlessly.\n"
        "The summary below will REPLACE the full conversation history — the agent must "
        "be able to continue without re-reading files or re-running completed commands.\n\n"
        "STRUCTURED SUMMARY (use these exact headings):\n\n"
        "## Current Goal\n"
        "What the user asked for. Is it done, in progress, or blocked?\n\n"
        "## Files Examined\n"
        "For each file read: path + key findings (function names, bugs found, patterns noted). "
        "If the file was NOT fully read, note what remains.\n\n"
        "## Files Modified\n"
        "For each edit: path + what changed + why. Include before/after for critical changes.\n\n"
        "## Commands Executed\n"
        "Command + result summary (exit code, key output lines, errors if any).\n\n"
        "## Key Decisions & Conclusions\n"
        "What was decided? What was ruled out? What approach was chosen and why?\n\n"
        "## Errors & Fixes\n"
        "What broke, why, and how it was fixed.\n\n"
        "## Remaining Work\n"
        "Explicit checklist of what still needs to be done.\n\n"
        "## User Constraints\n"
        "Any preferences, rules, or constraints the user mentioned.\n\n"
        "CONVERSATION:\n" + conversation
    )
    response = _get_client().chat.completions.create(
        model=_get_summary_model(),
        messages=[{"role": "system", "content": "You are a senior tech lead tracking agent state. Your summaries must be structured, factual, and complete. Never omit file paths or function names."},
                  {"role": "user", "content": prompt}],
        max_completion_tokens=2500
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
    print(f"  \033[33m[{label}] condensed to 1 summary + last 10 messages (was {len(messages)} total)\033[0m")
    return [{"role": "user", "content": f"[Reactive compact]\n\n{summary}"},
            *messages[-10:]]


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
    if not base.exists():
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
