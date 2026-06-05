"""
CLI slash-command system for cc_mine.
Reference: Claude Code's common slash commands.

Prefix: / (e.g. /help, /clear, /model chat)
Commands are handled locally — no LLM call made.
"""

import os
import time
from typing import Callable

# ── Command result ──
# (response_text, should_exit: bool)
CmdResult = tuple[str, bool]

# ── Registry ──
# name -> {help, handler, aliases}
_command_registry: dict[str, dict] = {}


def register(name: str, help_text: str, aliases: list[str] | None = None):
    """Decorator: register a slash command handler."""
    def decorator(fn: Callable[..., CmdResult]):
        _command_registry[name] = {
            "help": help_text,
            "handler": fn,
            "aliases": aliases or [],
        }
        return fn
    return decorator


# ── Shared state (set by main.py) ──
_shared_state: dict = {}  # keys: history, context, workdir, session_id


def set_shared_state(history: list, context: dict, workdir, session_id: str = ""):
    """Called by main.py each loop iteration to keep CLI commands in sync."""
    _shared_state["history"] = history
    _shared_state["context"] = context
    _shared_state["workdir"] = workdir
    _shared_state["session_id"] = session_id


def _h() -> list:
    return _shared_state.get("history", [])


def _ctx() -> dict:
    return _shared_state.get("context", {})


def _wd():
    return _shared_state.get("workdir")


def _sid() -> str:
    return _shared_state.get("session_id", "")


# ═══════════════════════════════════════════════════════════════
# Command handlers
# ═══════════════════════════════════════════════════════════════

@register("help", "Show this help message", aliases=["h", "?"])
def cmd_help(args: str) -> CmdResult:
    """Show available commands with descriptions."""
    seen = set()
    lines = [
        "\033[36m═══ cc_mine CLI Commands ═══\033[0m",
        "Prefix with / to invoke (e.g. /help, /clear)",
        "",
    ]
    # Sort by name, dedupe aliases
    for name in sorted(_command_registry.keys()):
        info = _command_registry[name]
        aliases_str = ""
        if info.get("aliases"):
            aliases_str = " (" + ", ".join(f"/{a}" for a in info["aliases"]) + ")"
        lines.append(f"  \033[33m/{name}\033[0m{aliases_str}  — {info['help']}")

    lines.append("")
    lines.append("Any other input is sent to the LLM agent.")
    return "\n".join(lines), False


@register("clear", "Clear conversation history (keeps system prompt)", aliases=["c"])
def cmd_clear(args: str) -> CmdResult:
    hist = _h()
    count = len(hist)
    hist.clear()
    return f"\033[32mCleared {count} messages. Context reset.\033[0m", False


@register("exit", "Exit cc_mine", aliases=["quit", "q"])
def cmd_exit(args: str) -> CmdResult:
    return "\033[90mGoodbye.\033[0m", True


@register("model", "Show or switch cloud model. For local models use /ollama", aliases=["m"])
def cmd_model(args: str) -> CmdResult:
    try:
        from call_llm import get_provider_info
        info = get_provider_info()
    except ImportError:
        info = {"provider": "?", "model": os.environ.get("PRIMARY_MODEL", "?"), "base_url": "?"}

    if not args.strip():
        lines = [
            f"Provider: \033[33m{info.get('provider', '?')}\033[0m",
            f"Model:    \033[32m{info.get('model', '?')}\033[0m",
            f"Base URL: \033[90m{info.get('base_url', '?')}\033[0m",
        ]
        if info.get("provider") == "ollama":
            lines.append(f"\nCloud fallback: {info.get('cloud_model', '?')}")
            lines.append("Use /ollama off to switch back to cloud.")
        else:
            lines.append("\nUse /ollama list to see local models.")
        return "\n".join(lines), False

    new_model = args.strip()
    os.environ["PRIMARY_MODEL"] = new_model
    # Sync ErrorRecovery's cached PRIMARY_MODEL
    try:
        import ErrorRecovery as _er
        _er.PRIMARY_MODEL = new_model
    except ImportError:
        pass
    current = info.get("model", "?")
    return f"Model changed: \033[90m{current}\033[0m → \033[32m{new_model}\033[0m\n(Next LLM call will use the new model)", False


@register("ollama", "Use local Ollama models. /ollama list | /ollama <model> | /ollama off")
def cmd_ollama(args: str) -> CmdResult:
    """Manage local Ollama models."""
    try:
        from call_llm import list_ollama_models, switch_to_ollama, switch_to_cloud, get_provider_info
    except ImportError:
        return "(provider switching unavailable)", False

    sub = args.strip().lower()

    # /ollama (no args) — show status
    if not sub:
        info = get_provider_info()
        lines = [
            f"Provider: \033[33m{info.get('provider', '?')}\033[0m",
            f"Model:    \033[32m{info.get('model', '?')}\033[0m",
            f"Base URL: \033[90m{info.get('base_url', '?')}\033[0m",
            "",
        ]

        # Try to list models
        models = list_ollama_models()
        if models:
            lines.append(f"\033[36mAvailable Ollama models ({len(models)}):\033[0m")
            for m in models[:30]:
                mark = " \033[32m← current\033[0m" if m == info.get("model") else ""
                lines.append(f"  {m}{mark}")
            if len(models) > 30:
                lines.append(f"  ... and {len(models) - 30} more")
        elif info.get("provider") == "ollama":
            lines.append("\033[31mOllama not reachable at localhost:11434\033[0m")
            lines.append("Is Ollama running? Run: ollama serve")
        else:
            lines.append("Ollama not connected. Use /ollama list to scan for models.")

        lines.append("")
        lines.append("Usage: \033[33m/ollama list\033[0m  — refresh model list")
        lines.append("       \033[33m/ollama <name>\033[0m  — switch to that model")
        lines.append("       \033[33m/ollama off\033[0m    — switch back to cloud API")
        return "\n".join(lines), False

    # /ollama list — refresh and show models
    if sub == "list":
        models = list_ollama_models()
        if not models:
            return (
                "\033[31mCannot reach Ollama at http://localhost:11434\033[0m\n"
                "Make sure Ollama is running:\n"
                "  • Install: https://ollama.com/download\n"
                "  • Start:   ollama serve\n"
                "  • Pull:    ollama pull qwen3\n"
            ), False

        info = get_provider_info()
        current_model = info.get("model", "")
        lines = [f"\033[36mOllama Models ({len(models)}):\033[0m"]
        for m in models[:40]:
            mark = " \033[32m← current\033[0m" if m == current_model else ""
            lines.append(f"  {m}{mark}")
        if len(models) > 40:
            lines.append(f"  ... and {len(models) - 40} more")
        lines.append(f"\nSwitch: /ollama <model_name>")
        return "\n".join(lines), False

    # /ollama off — switch back to cloud
    if sub == "off":
        return switch_to_cloud(), False

    # /ollama <model_name> — switch to that model
    models = list_ollama_models()
    if not models:
        return (
            "\033[31mCannot reach Ollama at http://localhost:11434\033[0m\n"
            "Start Ollama first, then try again."
        ), False

    # Try exact match first, then partial
    if sub in models:
        return switch_to_ollama(sub), False

    # Partial match
    matches = [m for m in models if sub in m]
    if len(matches) == 1:
        return switch_to_ollama(matches[0]), False
    elif len(matches) > 1:
        lines = [f"Multiple matches for '{sub}':"]
        for m in matches[:10]:
            lines.append(f"  {m}")
        lines.append("Be more specific: /ollama <exact_name>")
        return "\n".join(lines), False

    return f"\033[31mModel '{sub}' not found.\033[0m\nUse /ollama list to see available models.", False


@register("usage", "Show token usage for this session", aliases=["cost"])
def cmd_usage(args: str) -> CmdResult:
    try:
        from call_llm import get_session_usage
        tokens, calls = get_session_usage()
        lines = [
            f"\033[36m═══ Session Usage ═══\033[0m",
            f"  LLM calls:     {calls}",
            f"  Est. tokens:   ~{tokens:,}",
            f"  Messages:      {len(_h())}",
        ]
        hist = _h()
        if hist:
            tool_msgs = sum(1 for m in hist if m.get("role") == "tool")
            assistant_msgs = sum(1 for m in hist if m.get("role") == "assistant")
            lines.append(f"  Tool results:  {tool_msgs}")
            lines.append(f"  Asst replies:  {assistant_msgs}")
        return "\n".join(lines), False
    except ImportError:
        return "(usage tracking unavailable)", False


@register("skills", "List available skills", aliases=["skill"])
def cmd_skills(args: str) -> CmdResult:
    try:
        from skill_load import SKILL_REGISTRY, scan_skills
        if not SKILL_REGISTRY:
            scan_skills()
        if not SKILL_REGISTRY:
            return "(no skills found in skills/ directory)", False
        lines = ["\033[36m═══ Skills ═══\033[0m"]
        for name, info in sorted(SKILL_REGISTRY.items()):
            lines.append(f"  \033[33m{name}\033[0m — {info.get('description', '')[:100]}")
        return "\n".join(lines), False
    except ImportError:
        return "(skill system unavailable)", False


@register("tools", "List available tools (built-in + MCP)", aliases=["tool"])
def cmd_tools(args: str) -> CmdResult:
    try:
        from tool_registry import BUILTIN_TOOLS
        import mcp
        lines = ["\033[36m═══ Built-in Tools ═══\033[0m"]
        for t in BUILTIN_TOOLS:
            fn = t.get("function", t)
            name = fn.get("name", "?")
            desc = fn.get("description", "")[:80]
            lines.append(f"  \033[33m{name}\033[0m — {desc}")

        mcp_names = list(mcp.mcp_clients.keys()) if hasattr(mcp, 'mcp_clients') else []
        if mcp_names:
            lines.append(f"\n\033[36m═══ MCP Connected ═══\033[0m")
            for n in mcp_names:
                lines.append(f"  \033[32m{n}\033[0m")
        return "\n".join(lines), False
    except ImportError:
        return "(tool registry unavailable)", False


@register("sessions", "List saved sessions", aliases=["session"])
def cmd_sessions(args: str) -> CmdResult:
    try:
        from session import list_sessions
        sessions = list_sessions(_wd())
        if not sessions:
            return "(no saved sessions)", False
        lines = ["\033[36m═══ Saved Sessions ═══\033[0m"]
        for s in sessions[:20]:
            sid = s.get("session_id", "?")
            label = s.get("label", "")
            count = s.get("message_count", 0)
            created = s.get("created", "")[:16]
            crashed = " \033[31m[CRASHED]\033[0m" if s.get("crashed") else ""
            lines.append(f"  \033[33m{sid}\033[0m  {count} msgs  {created}{crashed}")
            if label:
                lines.append(f"    label: {label}")
        return "\n".join(lines), False
    except ImportError:
        return "(session system unavailable)", False


@register("resume", "Resume a saved session. Usage: /resume <session_id>")
def cmd_resume(args: str) -> CmdResult:
    sid = args.strip()
    if not sid:
        return "Usage: /resume <session_id>\nUse /sessions to list available sessions.", False

    try:
        from session import load_session
        loaded = load_session(_wd(), sid)
        if not loaded:
            return f"\033[31mSession '{sid}' not found.\033[0m", False
        new_hist, new_ctx = loaded
        old_hist = _h()
        old_hist.clear()
        old_hist.extend(new_hist)
        _ctx().clear()
        _ctx().update(new_ctx)
        _shared_state["session_id"] = sid
        return f"\033[32mResumed session {sid}: {len(new_hist)} messages restored.\033[0m", False
    except ImportError:
        return "(session system unavailable)", False


@register("save", "Force-save the current session. Usage: /save [label]")
def cmd_save(args: str) -> CmdResult:
    try:
        from session import save_session
        label = args.strip() or f"manual-save-{int(time.time())}"
        sid = save_session(_h(), _ctx(), _wd(), _sid(), label)
        return f"\033[32mSession saved: {sid}\033[0m  label: {label}", False
    except ImportError:
        return "(session system unavailable)", False


@register("compact", "Manually compact conversation context")
def cmd_compact(args: str) -> CmdResult:
    try:
        from memory import compact_history
        hist = _h()
        if len(hist) <= 5:
            return "Context is small — no compaction needed."
        hist[:] = compact_history(hist)
        return f"\033[32mCompacted. {len(hist)} messages remain.\033[0m", False
    except ImportError:
        return "(compaction unavailable)", False


@register("context", "Show context size and usage info")
def cmd_context(args: str) -> CmdResult:
    try:
        from memory import estimate_tokens
        hist = _h()
        est_tok = estimate_tokens(hist)
        from config import CONTEXT_LIMIT
        pct = (est_tok / CONTEXT_LIMIT * 100) if CONTEXT_LIMIT else 0
        bar_len = 30
        filled = min(int(pct / 100 * bar_len), bar_len)
        bar = "\033[32m" + "█" * filled + "\033[90m" + "░" * (bar_len - filled) + "\033[0m"

        lines = [
            f"\033[36m═══ Context ═══\033[0m",
            f"  Messages:    {len(hist)}",
            f"  Est. tokens: ~{est_tok:,} / {CONTEXT_LIMIT:,}",
            f"  [{bar}] {pct:.0f}%",
        ]
        return "\n".join(lines), False
    except ImportError:
        return "(context tracking unavailable)", False


@register("status", "Show current session status")
def cmd_status(args: str) -> CmdResult:
    hist = _h()
    ctx = _ctx()

    # Count tool calls by type
    tool_counts: dict[str, int] = {}
    for m in hist:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                name = tc.get("function", {}).get("name", "?")
                tool_counts[name] = tool_counts.get(name, 0) + 1

    lines = [
        f"\033[36m═══ Status ═══\033[0m",
        f"  Workdir:      {_wd()}",
        f"  Session:      {_sid() or '(new)'}",
        f"  Model:        {os.environ.get('PRIMARY_MODEL', 'default')}",
        f"  Messages:     {len(hist)}",
        f"  MCP servers:  {ctx.get('connected_mcp', [])}",
        f"  Teammates:    {ctx.get('active_teammates', [])}",
    ]

    # Planning state
    try:
        from planning import get_state as plan_state
        ps = plan_state()
        if ps != "idle":
            lines.append(f"  Plan mode:    \033[33m{ps}\033[0m")
    except ImportError:
        pass

    # Tool usage summary
    if tool_counts:
        lines.append(f"\n  \033[90m── Tool Calls (this session) ──\033[0m")
        for name, count in sorted(tool_counts.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"    {name}: {count}")

    return "\n".join(lines), False


@register("memory", "Show memory stats. Subcommands: add, search, delete")
def cmd_memory(args: str) -> CmdResult:
    try:
        from config import MEMORY_DIR
        if not MEMORY_DIR.exists():
            return "(no memories yet — create one with /memory-add)", False

        files = sorted(MEMORY_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return "(no memories yet)", False

        lines = [f"\033[36m═══ Memories ({len(files)}) ═══\033[0m"]
        for f in files[:20]:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))
            lines.append(f"  \033[33m{f.stem}\033[0m  ({ts})")
        if len(files) > 20:
            lines.append(f"  ... and {len(files) - 20} more")
        return "\n".join(lines), False
    except ImportError:
        return "(memory system unavailable)", False


@register("ccmine", "Show CC_MINE.md user preferences content")
def cmd_ccmine(args: str) -> CmdResult:
    from config import CC_MINE_MD_PATH, load_cc_mine_md
    if not CC_MINE_MD_PATH.exists():
        return ("CC_MINE.md not found at project root.\n"
                "Create one to set your preferences (language, coding style, rules).", False)
    content = load_cc_mine_md()
    if not content:
        return "CC_MINE.md is empty. Add your preferences to it.", False
    return f"\033[36m═══ CC_MINE.md ═══\033[0m\n{content[:3000]}", False


@register("skill-clear", "Clear loaded skill contexts to free memory")
def cmd_skill_clear(args: str) -> CmdResult:
    from skill_context import clear_skills, list_loaded_skills
    loaded = list_loaded_skills()
    if not loaded:
        return "No skills currently loaded.", False
    clear_skills()
    return f"Cleared {len(loaded)} loaded skill(s): {', '.join(loaded)}", False


@register("mode", "Show or set agent mode. /mode [auto|ask]")
def cmd_mode(args: str) -> CmdResult:
    from mode_manager import get_mode, set_mode
    if not args.strip():
        current = get_mode()
        return (f"\033[36m═══ Agent Mode ═══\033[0m\n"
                f"  Current: \033[{'32m' if current == 'auto' else '33m'}{current}\033[0m\n"
                f"  auto = proceed without asking\n"
                f"  ask  = confirm before files/bash/git/MCP\n"
                f"  Usage: /mode auto | /mode ask"), False
    result = set_mode(args.strip())
    return f"\033[32m{result}\033[0m", False


@register("skill-install", "Install a skill from URL. /skill-install <url> [name]")
def cmd_skill_install(args: str) -> CmdResult:
    parts = args.strip().split(maxsplit=1)
    if not parts or not parts[0]:
        return "Usage: /skill-install <url> [skill_name]", False
    url = parts[0]
    name = parts[1] if len(parts) > 1 else ""
    from skill_installer import install_skill_from_url
    from skill_load import scan_skills
    result = install_skill_from_url(url, name)
    scan_skills()
    return result, False


@register("debug-status", "Show debug failure tracking. /debug-status [reset]")
def cmd_debug_status(args: str) -> CmdResult:
    from debug_tracker import get_failure_count, reset_failures
    if args.strip() == "reset":
        prev = get_failure_count()
        reset_failures()
        return f"Reset. Previous count: {prev} failed attempts.", False
    count = get_failure_count()
    if count == 0:
        return "No failed debug attempts tracked.", False
    return (f"\033[33m{count} consecutive failed fix attempts.\033[0m\n"
            f"Use '/debug-status reset' to clear.", False)


@register("cache", "Show system prompt cache statistics")
def cmd_cache(args: str) -> CmdResult:
    try:
        from call_llm import get_cache_stats, get_session_usage
        hits, misses = get_cache_stats()
        tokens, calls = get_session_usage()
        hit_rate = f"{hits / max(hits + misses, 1) * 100:.0f}%"
        lines = [
            f"\033[36m═══ Cache Stats ═══\033[0m",
            f"  Hits: {hits}  Misses: {misses}  Rate: {hit_rate}",
            f"  Session tokens: ~{tokens}  Calls: {calls}",
        ]
        return "\n".join(lines), False
    except ImportError:
        return "(cache system unavailable)", False


@register("memory-add", "Add a memory. Usage: /memory-add <title> | <content>")
def cmd_memory_add(args: str) -> CmdResult:
    parts = args.split("|", 1)
    title = parts[0].strip()
    content = parts[1].strip() if len(parts) > 1 else ""
    if not title:
        return "Usage: /memory-add <title> | <content>", False

    try:
        from memory import add_memory
        return add_memory(title, content), False
    except ImportError:
        return "(memory system unavailable)", False


@register("memory-search", "Search memories. Usage: /memory-search <query>")
def cmd_memory_search(args: str) -> CmdResult:
    query = args.strip()
    if not query:
        return "Usage: /memory-search <query>", False

    try:
        from memory import search_memory
        return search_memory(query), False
    except ImportError:
        return "(memory system unavailable)", False


@register("tasks", "List current task board", aliases=["task"])
def cmd_tasks(args: str) -> CmdResult:
    try:
        from task import list_tasks
        tasks = list_tasks()
        if not tasks:
            return "(no tasks)", False
        lines = ["\033[36m═══ Task Board ═══\033[0m"]
        for t in tasks:
            status_icon = {"pending": "○", "in_progress": "●", "completed": "✓", "cancelled": "✗"}.get(t.status, "?")
            wt = f" [wt:{t.worktree}]" if getattr(t, 'worktree', None) else ""
            lines.append(f"  {status_icon} \033[33m{t.id}\033[0m: {t.subject} \033[90m[{t.status}]\033[0m{wt}")
        return "\n".join(lines), False
    except ImportError:
        return "(task system unavailable)", False


@register("crons", "List scheduled cron jobs", aliases=["cron"])
def cmd_crons(args: str) -> CmdResult:
    try:
        from CronScheduler import run_list_crons
        result = run_list_crons()
        if result == "No cron jobs.":
            return "(no cron jobs)", False
        return f"\033[36m═══ Cron Jobs ═══\033[0m\n{result}", False
    except ImportError:
        return "(cron system unavailable)", False


@register("plan", "Enter planning mode. Usage: /plan [goal description]")
def cmd_plan(args: str) -> CmdResult:
    goal = args.strip()
    if not goal:
        goal = "Explore the codebase and design an implementation plan before making any changes."

    # Inject a user message so the agent naturally enters plan mode via the LLM
    hist = _h()
    hist.append({"role": "user", "content": (
        f"[System] The user wants you to enter planning mode.\n"
        f"Goal: {goal}\n\n"
        f"Call enter_plan_mode with this goal. "
        f"Explore the codebase (read_file, glob, web_search), design a plan, "
        f"then call submit_plan when ready. "
        f"Write tools (bash, write_file, edit_file) are BLOCKED during planning — "
        f"this is READ-ONLY exploration. The user will approve your plan via CLI."
    )})
    return (
        f"\033[33mEntering planning mode...\033[0m\n"
        f"Goal: {goal}\n"
        f"The agent will explore and submit a plan for your approval.\n"
        f"Use \033[32m/plan-approve\033[0m or \033[31m/plan-reject\033[0m when the plan is ready."
    ), False


@register("plan-approve", "Approve the submitted plan and begin execution")
def cmd_plan_approve(args: str) -> CmdResult:
    try:
        from planning import approve_plan, get_state as plan_state
        if plan_state() != "plan_ready":
            return f"No plan awaiting approval (current state: {plan_state()}). Use /plan to start planning.", False

        msg = approve_plan()
        # Inject approval as a user message so the agent sees it next turn
        hist = _h()
        hist.append({"role": "user", "content": msg})
        return "\033[32mPlan approved! The agent will begin execution next turn.\033[0m", False
    except ImportError:
        return "(planning system unavailable)", False


@register("plan-reject", "Reject the submitted plan. Usage: /plan-reject [feedback]")
def cmd_plan_reject(args: str) -> CmdResult:
    try:
        from planning import reject_plan, get_state as plan_state
        if plan_state() != "plan_ready":
            return f"No plan awaiting approval (current state: {plan_state()}).", False

        feedback = args.strip()
        msg = reject_plan(feedback)
        # Inject rejection/feedback as a user message
        hist = _h()
        hist.append({"role": "user", "content": msg})
        if feedback:
            return f"\033[33mFeedback sent. The agent will revise the plan.\033[0m", False
        else:
            return f"\033[31mPlan rejected. The agent can revise or exit plan mode.\033[0m", False
    except ImportError:
        return "(planning system unavailable)", False


@register("plan-exit", "Force exit plan mode (if agent is stuck)")
def cmd_plan_exit(args: str) -> CmdResult:
    try:
        from planning import exit_plan_mode, get_state as plan_state
        ps = plan_state()
        if ps == "idle":
            return "Not in plan mode.", False
        msg = exit_plan_mode("user forced exit via /plan-exit")
        hist = _h()
        hist.append({"role": "user", "content": msg})
        return "\033[32mPlan mode exited. You can now continue normally.\033[0m", False
    except ImportError:
        return "(planning system unavailable)", False


@register("config", "Show current configuration")
def cmd_config(args: str) -> CmdResult:
    try:
        import config
        lines = [
            f"\033[36m═══ Configuration ═══\033[0m",
            f"  WORKDIR:           {config.WORKDIR}",
            f"  DEFAULT_MAX_TOKENS: {config.DEFAULT_MAX_TOKENS}",
            f"  ESCALATED_MAX_TOKENS: {config.ESCALATED_MAX_TOKENS}",
            f"  CONTEXT_LIMIT:     {config.CONTEXT_LIMIT}",
            f"  MAX_TURNS:         {config.MAX_TURNS}",
            f"  MAX_RETRIES:       {config.MAX_RETRIES}",
            f"  PRIMARY_MODEL:     {os.environ.get('PRIMARY_MODEL', 'default')}",
            f"  LLM_BASE_URL:      {os.environ.get('LLM_BASE_URL', 'default')}",
        ]
        return "\n".join(lines), False
    except ImportError:
        return "(config unavailable)", False


@register("worktrees", "List git worktrees", aliases=["wt", "worktree"])
def cmd_worktrees(args: str) -> CmdResult:
    try:
        from config import WORKTREES_DIR
        if not WORKTREES_DIR.exists():
            return "(no worktrees)", False
        entries = sorted(WORKTREES_DIR.iterdir())
        if not entries:
            return "(no worktrees)", False
        lines = ["\033[36m═══ Worktrees ═══\033[0m"]
        for e in entries:
            lines.append(f"  {e.name}")
        return "\n".join(lines), False
    except ImportError:
        return "(worktree system unavailable)", False


# ── Multimodal file/image input ──

@register("image", "Attach an image to the next message. Usage: /image <path>")
def cmd_image(args: str) -> CmdResult:
    path = args.strip()
    if not path:
        return "Usage: /image <path/to/image.png>\nSupported: png, jpg, gif, webp, bmp", False

    try:
        from multimodal import attach_image, list_pending
        result = attach_image(path)
        pending_info = list_pending()
        return f"{result}\n\n{pending_info}\n\nType your message now — the image will be included.", False
    except ImportError:
        return "(multimodal support unavailable)", False


@register("file", "Attach a file to the next message. Usage: /file <path>\nSupports images, PDFs, text files, and more.")
def cmd_file(args: str) -> CmdResult:
    path = args.strip()
    if not path:
        return "Usage: /file <path>\nSupports: images (png/jpg/gif), PDFs, text files (.py/.md/.json...)", False

    try:
        from multimodal import attach_file, list_pending
        result = attach_file(path)
        pending_info = list_pending()
        return f"{result}\n\n{pending_info}\n\nType your message now — the file will be included.", False
    except ImportError:
        return "(multimodal support unavailable)", False


@register("attachments", "Show pending file/image attachments", aliases=["atts"])
def cmd_attachments(args: str) -> CmdResult:
    try:
        from multimodal import list_pending, clear_pending, pending_count

        if args.strip() == "clear":
            n = clear_pending()
            return f"Cleared {n} pending attachment(s).", False

        info = list_pending()
        return info, False
    except ImportError:
        return "(multimodal support unavailable)", False


# ── Collapsible Output ──

@register("expand", "Expand the Nth tool output from the last turn. /expand 1")
def cmd_expand(args: str) -> CmdResult:
    try:
        index = int(args.strip())
    except ValueError:
        return "Usage: /expand <N> (e.g. /expand 1)", False

    from output_manager import get_output, output_count
    from terminal_renderer import render_tool_output

    n = output_count()
    if n == 0:
        return "No tool outputs from the last turn.", False
    entry = get_output(index)
    if entry is None:
        return f"Invalid index: {index}. Valid range: 1-{n}", False
    render_tool_output(entry["name"], entry["output"],
                       collapsed=False, output_index=index - 1)
    return "", False


@register("collapse", "Collapse the Nth tool output. /collapse 1")
def cmd_collapse(args: str) -> CmdResult:
    try:
        index = int(args.strip())
    except ValueError:
        return "Usage: /collapse <N> (e.g. /collapse 1)", False

    from output_manager import get_output, output_count
    from terminal_renderer import render_tool_output

    n = output_count()
    if n == 0:
        return "No tool outputs from the last turn.", False
    entry = get_output(index)
    if entry is None:
        return f"Invalid index: {index}. Valid range: 1-{n}", False
    preview = '\n'.join(entry["output"].split('\n')[:8])
    render_tool_output(entry["name"], preview,
                       collapsed=True, output_index=index - 1,
                       full_output=entry["output"])
    return "", False


@register("toggle-collapse", "Toggle default collapse/expand for tool outputs")
def cmd_toggle_collapse(args: str) -> CmdResult:
    from output_manager import toggle_collapse
    new_state = toggle_collapse()
    state_str = "collapsed (preview only)" if new_state else "expanded (full output)"
    return f"Tool output default: \033[33m{state_str}\033[0m", False


# ═══════════════════════════════════════════════════════════════
# Dispatch
# ═══════════════════════════════════════════════════════════════

def _find_command(name: str) -> dict | None:
    """Find a command by name or alias."""
    if name in _command_registry:
        return _command_registry[name]
    for cmd_name, info in _command_registry.items():
        if name in (info.get("aliases") or []):
            return info
    return None


def handle_cli_command(line: str) -> CmdResult:
    """
    Parse and dispatch a slash command.

    Args:
        line: The full input line, e.g. "/model gpt-4" or "/clear"

    Returns:
        (response: str, should_exit: bool)
    """
    line = line.strip()
    if not line.startswith("/"):
        return (f"Not a command: {line}", False)

    # Remove the leading /
    rest = line[1:]

    # Split into command name and args
    parts = rest.split(None, 1)
    cmd_name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    if not cmd_name:
        return ("Type /help for available commands.", False)

    info = _find_command(cmd_name)
    if not info:
        # Fuzzy suggestion
        suggestions = [n for n in _command_registry if n.startswith(cmd_name[:2])]
        hint = ""
        if suggestions:
            hint = f" Did you mean: {', '.join('/' + s for s in suggestions[:3])}?"
        return (f"\033[31mUnknown command: /{cmd_name}\033[0m{hint}\nType /help for available commands.", False)

    try:
        return info["handler"](args)
    except Exception as e:
        return (f"\033[31mCommand error ({cmd_name}): {e}\033[0m", False)
