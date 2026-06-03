# ── Hooks + Permission Pipeline ──

# Hooks are intentionally outside tool handlers. The loop can add permission,
# logging, and stop behavior without changing each individual tool.
#
# Two roles per event, enforced by the trigger logic:
#   GATEKEEPER — runs first. If any returns non-None → short-circuit, block action.
#   OBSERVER   — runs after all gatekeepers pass. Never short-circuits.
#
# This guarantees permission checks always run BEFORE logging,
# regardless of registration order.

import json as _json
from config import WORKDIR
from tools.file_ops import safe_path
from terminal_renderer import render_tool_execution as _render_tc, render_info, render_warning

HOOKS = {
    "UserPromptSubmit": {"gatekeepers": [], "observers": []},
    "PreToolUse":       {"gatekeepers": [], "observers": []},
    "PostToolUse":      {"gatekeepers": [], "observers": []},
    "Stop":             {"gatekeepers": [], "observers": []},
}


class _ToolBlock:
    """Normalize dict / OpenAI tool_call → consistent .name / .id / .input access."""
    def __init__(self, raw):
        if isinstance(raw, dict):
            self.name = raw.get("name", "")
            self.id = raw.get("id", "")
            self.input = raw.get("args", {}) or raw.get("input", {})
        else:
            # OpenAI ChatCompletionMessageFunctionToolCall (pydantic model)
            fn = raw.function
            self.name = fn.name
            self.id = raw.id
            try:
                self.input = _json.loads(fn.arguments)
            except (TypeError, _json.JSONDecodeError):
                self.input = {}
def register_hook(event: str, callback, role: str = "observer"):
    """Register a hook. role='gatekeeper' runs first and can short-circuit; 'observer' always runs."""
    HOOKS[event][("gatekeepers" if role == "gatekeeper" else "observers")].append(callback)


def trigger_hooks(event: str, *args):
    # 1. Gatekeepers first — any non-None return blocks the action
    for callback in HOOKS[event]["gatekeepers"]:
        result = callback(*args)
        if result is not None:
            return result
    # 2. Observers second — always run, cannot block
    for callback in HOOKS[event]["observers"]:
        callback(*args)
    return None

DENY_LIST = [
    "rm -rf /", "rm -rf ~", "rm -rf .",
    "sudo ", "shutdown", "reboot", "halt",
    "mkfs", "dd if=", "mkswap",
    ":(){ :|:& };:",  # fork bomb
    "chmod 777 /", "chmod -R 777 /",
    "chmod 777 ~", "chmod -R 777 ~",
]

DESTRUCTIVE = [
    "rm ", "> /etc/", ">> /etc/",
    "chmod 777", "chown ", "chgrp ",
    "git push --force", "git push -f",
    "git reset --hard", "git clean -fdx", "git branch -D",
    "| bash", "| sh", "| /bin/bash", "| /bin/sh",
    "eval ",
]

_PERMISSIONS_CACHE = None
_PERMISSIONS_MTIME = 0

# ── Bash rate limiter ──
_TOOL_COUNTS: dict[str, int] = {}
_BASH_MAX_PER_TURN = 50


def _reset_tool_counts():
    """Reset per-turn tool counters. Called at start of each agent turn."""
    _TOOL_COUNTS.clear()


def _load_permissions() -> list[dict]:
    """Load permission rules from WORKDIR/.cc_mine/permissions.json (with hot-reload)."""
    global _PERMISSIONS_CACHE, _PERMISSIONS_MTIME
    path = WORKDIR / ".cc_mine" / "permissions.json"
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    if _PERMISSIONS_CACHE is not None and mtime == _PERMISSIONS_MTIME:
        return _PERMISSIONS_CACHE
    try:
        rules = _json.loads(path.read_text(encoding="utf-8"))
        _PERMISSIONS_CACHE = rules
        _PERMISSIONS_MTIME = mtime
        return rules
    except Exception:
        return []


def _match_rule(rules: list[dict], tool: str, operand: str) -> str | None:
    """Check rules top-to-bottom. Returns action ('allow','deny','ask') or None."""
    import fnmatch
    for rule in rules:
        r_tool = rule.get("tool", "*")
        r_pattern = rule.get("pattern", "*")
        if r_tool != "*" and r_tool != tool:
            continue
        if fnmatch.fnmatch(operand, r_pattern):
            return rule.get("action", "ask"), rule.get("reason", "")
    return None


def permission_hook(block):
    b = _ToolBlock(block)
    command = b.input.get("command", b.input.get("path", ""))
    reason = ""

    # 1. Config-file rules (highest priority)
    rules = _load_permissions()
    if rules:
        match = _match_rule(rules, b.name, command)
        if match:
            action, reason = match
            if action == "allow":
                return None
            if action == "deny":
                return f"Permission denied: {reason or 'blocked by permissions.json'}"

    # 2. Built-in deny list
    if b.name == "bash":
        cmd = b.input.get("command", "")
        for pattern in DENY_LIST:
            if pattern in cmd:
                return f"Permission denied: '{pattern}' is on the deny list"
        if any(token in cmd for token in DESTRUCTIVE):
            print(f"\n\033[33m[permission] destructive command\033[0m")
            print(f"  {cmd}")
            choice = input("  Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return "Permission denied by user"

    # 2b. Bash rate limiter — prevent runaway tool call loops
    if b.name == "bash":
        _TOOL_COUNTS["bash"] = _TOOL_COUNTS.get("bash", 0) + 1
        if _TOOL_COUNTS["bash"] > _BASH_MAX_PER_TURN:
            return (f"Rate limit: max {_BASH_MAX_PER_TURN} bash calls per turn. "
                    f"Consider batching commands or delegating to a subagent.")

    # 3. Path escape check
    if b.name in ("write_file", "edit_file"):
        path = b.input.get("path", "")
        try:
            safe_path(path)
        except Exception:
            return f"Permission denied: path escapes workspace: {path}"

        # 3b. Sensitive file path check — block writing to protected paths
        SENSITIVE_PATTERNS = [
            ".env", ".env.", "credentials", "secrets", "secret",
            ".ssh/", "id_rsa", "id_ed25519", "id_ecdsa",
            ".git/config", ".gitmodules",
            "/etc/passwd", "/etc/shadow", "/etc/hosts",
        ]
        path_lower = path.lower().replace("\\", "/")
        for sp in SENSITIVE_PATTERNS:
            if sp in path_lower:
                print(f"\n\033[33m[permission] writing to sensitive path: {path}\033[0m")
                print(f"  Matches sensitive pattern: {sp}")
                choice = input("  Allow? [y/N] ").strip().lower()
                if choice not in ("y", "yes"):
                    return f"Permission denied: writing to sensitive path '{path}'"

    # 3c. Ask mode check (if mode_manager is available)
    try:
        from mode_manager import get_mode, ASK_TOOLS
        mode = get_mode()
        if mode == "ask" and b.name in ASK_TOOLS:
            desc = _describe_tool(b)
            print(f"\n\033[33m[ask mode] About to execute:\033[0m")
            print(f"  {desc}")
            print(f"  \033[90mAllow? [y/N/auto] \033[0m", end="")
            choice = input().strip().lower()
            if choice == "auto":
                from mode_manager import set_mode
                set_mode("auto")
                print(f"  \033[32mSwitched to auto mode. Proceeding.\033[0m")
            elif choice in ("y", "yes"):
                pass  # allow this one
            else:
                return "Permission denied by user (ask mode)"
    except ImportError:
        pass  # mode_manager not installed yet

    # 4. MCP deploy check
    if b.name.startswith("mcp__") and "deploy" in b.name:
        print(f"\n\033[33m[permission] MCP destructive-looking tool: {b.name}\033[0m")
        choice = input("  Allow? [y/N] ").strip().lower()
        if choice not in ("y", "yes"):
            return "Permission denied by user"

    return None

def git_safety_hook(block):
    """Gatekeeper: block destructive git operations unless explicitly allowed."""
    b = _ToolBlock(block)
    if b.name not in ("bash", "git"):
        return None

    cmd = b.input.get("command", "")
    if b.name == "git":
        # Build command string from args list
        args = b.input.get("args", [])
        cmd = "git " + " ".join(args)

    GIT_DESTRUCTIVE = [
        ("git push --force", "force push to remote"),
        ("git push -f", "force push to remote"),
        ("git push --delete", "delete remote branch"),
        ("git reset --hard", "hard reset (discards changes)"),
        ("git clean -fdx", "clean all untracked files"),
        ("git branch -D", "force delete branch"),
        ("git stash drop", "drop stashed changes"),
        ("git rebase --onto", "potentially destructive rebase"),
    ]
    for pattern, desc in GIT_DESTRUCTIVE:
        if pattern in cmd:
            print(f"\n\033[33m[git safety] Destructive git operation: {desc}\033[0m")
            print(f"  {cmd[:120]}")
            choice = input("  Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return f"Git operation denied: {desc}"
            return None  # explicitly allowed

    return None


def _describe_tool(b):
    """Return a human-readable one-liner describing what a tool call is doing."""
    a = b.input
    tool = b.name
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    RST = "\033[0m"
    if tool == "bash":
        cmd = str(a.get("command", ""))[:80]
        # Highlight file paths in the command for quick scanning
        import re as _re
        highlighted = _re.sub(
            r'(\S*[/\\]\S*|\S+\.(py|js|ts|json|yaml|yml|toml|cfg|ini|md|txt|csv|html|css|sh|bat))',
            lambda m: f"{YELLOW}{m.group(1)}{RST}",
            cmd
        )
        return f"bash: {highlighted}"
    elif tool == "read_file":
        p = a.get("path", "?")
        off = f" +{a['offset']}" if a.get("offset") else ""
        lim = f" x{a['limit']}" if a.get("limit") else ""
        return f"read: {YELLOW}{p}{RST}{off}{lim}"
    elif tool == "write_file":
        size = len(str(a.get("content", "")))
        return f"write: {YELLOW}{a.get('path', '?')}{RST} ({size} bytes)"
    elif tool == "edit_file":
        return f"edit: {YELLOW}{a.get('path', '?')}{RST}"
    elif tool == "glob":
        pat = a.get("pattern", a.get("path", "?"))
        return f"glob: {YELLOW}{pat}{RST}"
    elif tool == "grep":
        pat = a.get("pattern", "?")
        fp = a.get("path", ".")
        return f"grep: /{pat}/ in {YELLOW}{fp}{RST}"
    elif tool == "todo_write":
        todos = a.get("todos") or []
        active = sum(1 for t in todos if t.get("status") == "in_progress")
        return f"todo: {active}/{len(todos)} active"
    elif tool == "task":
        return f"subagent: {str(a.get('description', ''))[:80]}"
    elif tool == "create_task":
        return f"+task: {a.get('subject', '?')}"
    elif tool == "claim_task":
        return f"claim: {a.get('task_id', '?')}"
    elif tool == "complete_task":
        return f"done: {a.get('task_id', '?')}"
    elif tool == "spawn_teammate":
        return f"+teammate: {a.get('name', '?')} as {a.get('role', '?')}"
    elif tool == "send_message":
        return f"msg → {a.get('to', '?')}: {str(a.get('content', ''))[:50]}"
    elif tool == "request_shutdown":
        return f"shutdown: {a.get('teammate', '?')}"
    elif tool == "review_plan":
        action = "approve" if a.get("approve") else "reject"
        return f"{action} plan: {a.get('request_id', '?')}"
    elif tool == "create_worktree":
        return f"+worktree: {a.get('name', '?')}"
    elif tool == "remove_worktree":
        return f"-worktree: {a.get('name', '?')}"
    elif tool == "schedule_cron":
        return f"cron: {a.get('cron', '?')} — {str(a.get('prompt', ''))[:40]}"
    elif tool == "connect_mcp":
        return f"+mcp: {a.get('name', '?')}"
    elif tool == "disconnect_mcp":
        return f"-mcp: {a.get('name', '?')}"
    elif tool == "list_mcp_servers":
        return "list MCP servers"
    elif tool == "load_skill":
        return f"skill: {a.get('name', '?')}"
    elif tool == "compact":
        return "compact context"
    elif tool == "web_search":
        return f"search: {a.get('query', '?')[:50]}"
    elif tool == "web_fetch":
        return f"fetch: {a.get('url', '?')}"
    else:
        return tool


def log_hook(block):
    b = _ToolBlock(block)
    desc = _describe_tool(b)
    _render_tc(b.name, desc)
    return None


def large_output_hook(block, output):
    b = _ToolBlock(block)
    size = len(str(output))
    if size > 100000:
        render_warning(f"large output {b.name}: {size} chars ({size//1024}KB)")
    elif size > 10000:
        render_info(f"output {b.name}: {size} chars")
    return None


def user_prompt_hook(query: str):
    return None  # silent — the input prompt is already visible


def stop_hook(messages: list):
    tool_count = sum(1 for msg in messages if msg.get("role") == "tool")
    assistant_count = sum(1 for msg in messages if msg.get("role") == "assistant")
    render_info(f"turn end: {assistant_count} responses, {tool_count} tool results")
    return None

register_hook("UserPromptSubmit", user_prompt_hook, role="observer")
register_hook("PreToolUse", permission_hook, role="gatekeeper")
register_hook("PreToolUse", git_safety_hook, role="gatekeeper")
register_hook("PreToolUse", log_hook, role="observer")
register_hook("PostToolUse", large_output_hook, role="observer")
register_hook("Stop", stop_hook, role="observer")