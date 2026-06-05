# ── MCP System ──
#
# Supports two modes:
#   1. Mock servers (docs, deploy) — built-in, no external process needed
#   2. Real MCP stdio servers — subprocess-based, full MCP protocol handshake
#
# Real servers are configured in MCP_CONFIG dict below.
# Add any MCP-compatible CLI tool and it auto-discovers tools on connect.

import json
import re
import subprocess
import threading
import time

from tool_registry import BUILTIN_TOOLS


# ═══════════════════════════════════════════════════════════
# MCPClient — wraps one server's tool definitions + handlers
# ═══════════════════════════════════════════════════════════

class MCPClient:
    """Holds tool definitions and call handlers for one MCP server."""

    def __init__(self, name: str):
        self.name = name
        self.tools: list[dict] = []
        self._handlers: dict[str, callable] = {}
        self._proc: subprocess.Popen | None = None  # for real stdio servers

    def register(self, tool_defs: list[dict],
                 handlers: dict[str, callable],
                 proc: subprocess.Popen | None = None):
        self.tools = tool_defs
        self._handlers = handlers
        self._proc = proc

    def call_tool(self, tool_name: str, args: dict) -> str:
        handler = self._handlers.get(tool_name)
        if not handler:
            return f"MCP error: unknown tool '{tool_name}'"
        try:
            return handler(**args)
        except Exception as e:
            return f"MCP error: {e}"

    def close(self):
        """Terminate the subprocess if this is a real (stdio) server."""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()


# ── Global registry ──
mcp_clients: dict[str, MCPClient] = {}

_DISALLOWED_CHARS = re.compile(r'[^a-zA-Z0-9_-]')


def normalize_mcp_name(name: str) -> str:
    """Replace non [a-zA-Z0-9_-] with underscore."""
    return _DISALLOWED_CHARS.sub('_', name)


# ═══════════════════════════════════════════════════════════
# Real MCP: stdio subprocess + JSON-RPC handshake
# ═══════════════════════════════════════════════════════════

# Configuration for real MCP servers.
#
# Format options:
#   1. Simple: "name" -> list[str]  (command + args)
#      Example: "fetch": ["uvx", "mcp-server-fetch"]
#
#   2. Extended: "name" -> dict with command, args, env
#      Example: "github": {"command": "npx", "args": ["-y", "..."], "env": {"TOKEN": "xxx"}}
#
# Environment variables set here are merged into the subprocess environment.
# Leave values as empty strings to inherit from the parent shell environment.
# Add or remove entries here. The agent discovers tools automatically.
MCP_CONFIG: dict[str, list[str] | dict] = {
    "fetch":  ["uvx", "mcp-server-fetch"],

    # GitHub MCP — manage repos, issues, PRs, workflows
    # Requires: GITHUB_PERSONAL_ACCESS_TOKEN (classic PAT with repo/user/read:user scopes)
    # Generate token at: https://github.com/settings/tokens
    # Alternative: use @noushad999/github-mcp-server or the official github/github-mcp-server (Docker)
    "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {
            "GITHUB_PERSONAL_ACCESS_TOKEN": "",  # Set your GitHub PAT here or export in shell
        },
    },

    # Feishu (飞书) MCP — manage documents, Bitable, Wiki, tasks, calendar, contacts
    # Requires: Feishu app credentials from Feishu Open Platform
    # Create app and get credentials: https://open.feishu.cn/document/home/develop-a-bot-in-5-minutes/create-an-app
    # Package: https://github.com/cso1z/Feishu-MCP
    "feishu": {
        "command": "npx",
        "args": ["-y", "feishu-mcp@latest", "--stdio"],
        "env": {
            "FEISHU_APP_ID": "",          # 飞书应用 ID (形如 cli_xxxxxxxx)
            "FEISHU_APP_SECRET": "",      # 飞书应用密钥
            "FEISHU_AUTH_TYPE": "tenant", # tenant=应用级权限, user=用户级权限(需OAuth)
            "FEISHU_ENABLED_MODULES": "document,task,calendar,member",  # 启用模块
        },
    },
}

# Store active subprocesses for cleanup
_real_procs: dict[str, subprocess.Popen] = {}


def _normalize_mcp_entry(entry: list[str] | dict) -> tuple[list[str], dict[str, str]]:
    """Normalize MCP config entry to (command_list, env_dict).

    Supports both legacy list format and new dict format.
    Dict format: {"command": "npx", "args": ["-y", "pkg"], "env": {"KEY": "val"}}
    """
    if isinstance(entry, list):
        return list(entry), {}
    if isinstance(entry, dict):
        cmd = [entry.get("command", "")]
        cmd.extend(entry.get("args", []))
        env = dict(entry.get("env", {}))
        return cmd, env
    raise TypeError(f"Invalid MCP config entry type: {type(entry).__name__}")


def _send_mcp_request(proc: subprocess.Popen, method: str,
                      params: dict | None = None) -> dict:
    """Send a JSON-RPC request to an MCP stdio server. Returns the parsed response."""
    req_id = int(time.time() * 1000) % 100000
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {},
    }, ensure_ascii=False)

    try:
        proc.stdin.write(request + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        return {"error": f"Failed to write to MCP process: {e}"}

    try:
        line = proc.stdout.readline()
        if not line:
            return {"error": "MCP process closed stdout unexpectedly"}
        return json.loads(line)
    except (json.JSONDecodeError, OSError) as e:
        return {"error": f"Failed to read MCP response: {e}"}


def _make_real_mcp_handler(proc: subprocess.Popen, tool_name: str):
    """Return a closure that calls tools/call on the real MCP server."""
    def handler(**kwargs):
        resp = _send_mcp_request(proc, "tools/call", {
            "name": tool_name,
            "arguments": kwargs,
        })
        if "error" in resp:
            err = resp["error"]
            msg = err if isinstance(err, str) else err.get("message", str(err))
            return f"MCP error: {msg}"
        # Extract text from content array
        content = resp.get("result", {}).get("content", [])
        if not content:
            return "(MCP tool returned no content)"
        texts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                texts.append(c.get("text", ""))
            elif isinstance(c, str):
                texts.append(c)
        return "\n".join(texts) if texts else str(content)
    return handler


def _connect_real_mcp_stdio(command: list[str], name: str,
                           extra_env: dict[str, str] | None = None) -> tuple[str, MCPClient | None]:
    """Spawn a real MCP server process, handshake, discover tools.
    Args:
        command: The command + args list to spawn.
        name: Human-readable server name.
        extra_env: Additional env vars to merge into the subprocess env.
    Returns (message, client_or_None)."""
    import os as _os
    import platform

    # ── Build subprocess environment ──
    subprocess_env = _os.environ.copy()
    if extra_env:
        for k, v in extra_env.items():
            if v:  # Non-empty: set/override
                subprocess_env[k] = v
            # Empty value: let parent env prevail (already in os.environ.copy())

    # On Windows, try .cmd suffix if bare command fails
    cmd = list(command)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            env=subprocess_env,
        )
    except FileNotFoundError:
        if platform.system() == "Windows" and not cmd[0].endswith(".cmd"):
            cmd[0] = cmd[0] + ".cmd"
            try:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, bufsize=1,
                    env=subprocess_env,
                )
            except FileNotFoundError as e:
                return f"Error: MCP command not found — {e}", None
            except Exception as e:
                return f"Error starting MCP server '{name}': {e}", None
        else:
            return f"Error: MCP command not found — {e}", None
    except Exception as e:
        return f"Error starting MCP server '{name}': {e}", None

    # ── Step 1: Initialize handshake ──
    init_resp = _send_mcp_request(proc, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "cc_mine", "version": "1.0"},
    })
    if "error" in init_resp:
        proc.kill()
        return f"MCP init error: {init_resp['error']}", None

    server_info = init_resp.get("result", {}).get("serverInfo", {})
    server_name = server_info.get("name", name)
    print(f"  \033[31m[mcp] {server_name} v{server_info.get('version', '?')} "
          f"— handshake OK\033[0m")

    # ── Step 2: Send initialized notification ──
    try:
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {}
        }) + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        proc.kill()
        return "MCP error: process died during initialization", None

    # ── Step 3: Discover tools ──
    tools_resp = _send_mcp_request(proc, "tools/list")
    if "error" in tools_resp:
        proc.kill()
        return f"MCP tools/list error: {tools_resp['error']}", None

    tool_defs = tools_resp.get("result", {}).get("tools", [])
    if not tool_defs:
        proc.kill()
        return f"MCP server '{name}' reported 0 tools.", None

    # ── Step 4: Build handlers for every tool ──
    handlers = {}
    for td in tool_defs:
        tname = td.get("name", "unknown")
        handlers[tname] = _make_real_mcp_handler(proc, tname)

    client = MCPClient(name)
    client.register(tool_defs, handlers, proc=proc)
    _real_procs[name] = proc

    tool_names = [t.get("name", "?") for t in tool_defs]
    return (f"Connected to MCP server '{name}'. "
            f"Discovered {len(tool_defs)} tools: {', '.join(tool_names)}"), client


# ═══════════════════════════════════════════════════════════
# Mock servers (kept for backward compat + teaching)
# ═══════════════════════════════════════════════════════════

def _mock_server_docs():
    client = MCPClient("docs")
    client.register(
        tool_defs=[
            {"name": "search", "description": "Search documentation. (readOnly)",
             "inputSchema": {"type": "object",
                             "properties": {"query": {"type": "string"}},
                             "required": ["query"]}},
            {"name": "get_version", "description": "Get API version. (readOnly)",
             "inputSchema": {"type": "object", "properties": {},
                             "required": []}},
        ],
        handlers={
            "search": lambda query: f"[docs] Found 3 results for '{query}'",
            "get_version": lambda: "[docs] API v2.1.0",
        })
    return client


def _mock_server_deploy():
    client = MCPClient("deploy")
    client.register(
        tool_defs=[
            {"name": "trigger",
             "description": "Trigger a deployment. (destructive — requires approval in real CC)",
             "inputSchema": {"type": "object",
                             "properties": {"service": {"type": "string"}},
                             "required": ["service"]}},
            {"name": "status", "description": "Check deployment status. (readOnly)",
             "inputSchema": {"type": "object",
                             "properties": {"service": {"type": "string"}},
                             "required": ["service"]}},
        ],
        handlers={
            "trigger": lambda service: f"[deploy] Triggered: {service}",
            "status": lambda service: f"[deploy] {service}: running (v1.4.2)",
        })
    return client


MOCK_SERVERS = {
    "docs": _mock_server_docs,
    "deploy": _mock_server_deploy,
}


# ═══════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════

def connect_mcp(name: str) -> str:
    """Connect to an MCP server by name.
    Checks real MCP_CONFIG first, then MOCK_SERVERS."""
    if name in mcp_clients:
        return f"MCP server '{name}' already connected."

    # ── Try real MCP first ──
    if name in MCP_CONFIG:
        entry = MCP_CONFIG[name]
        command, env_vars = _normalize_mcp_entry(entry)
        print(f"  \033[31m[mcp] starting real server: {' '.join(command)}\033[0m")
        if env_vars:
            non_empty = {k: v for k, v in env_vars.items() if v}
            inherited = {k for k, v in env_vars.items() if not v}
            if non_empty:
                print(f"  \033[31m[mcp] env vars set: {', '.join(non_empty.keys())}\033[0m")
            if inherited:
                print(f"  \033[31m[mcp] env vars (from shell): {', '.join(inherited)}\033[0m")
        msg, client = _connect_real_mcp_stdio(command, name, extra_env=env_vars)
        if client is not None:
            mcp_clients[name] = client
            tool_names = [t.get("name", "?") for t in client.tools]
            print(f"  \033[31m[mcp] connected: {name} → {tool_names}\033[0m")
        return msg

    # ── Fall back to mock servers ──
    factory = MOCK_SERVERS.get(name)
    if factory:
        mcp_client = factory()
        mcp_clients[name] = mcp_client
        tool_names = [t["name"] for t in mcp_client.tools]
        print(f"  \033[31m[mcp] connected (mock): {name} → {tool_names}\033[0m")
        return (f"Connected to MCP server '{name}'. "
                f"Discovered {len(mcp_client.tools)} tools: {', '.join(tool_names)}")

    available = (list(MCP_CONFIG.keys()) + list(MOCK_SERVERS.keys()))
    return f"Unknown MCP server '{name}'. Available: {', '.join(available)}"


def disconnect_mcp(name: str) -> str:
    """Disconnect and clean up an MCP server."""
    if name not in mcp_clients:
        return f"MCP server '{name}' not connected."
    client = mcp_clients.pop(name)
    client.close()
    _real_procs.pop(name, None)
    print(f"  \033[31m[mcp] disconnected: {name}\033[0m")
    return f"Disconnected MCP server '{name}'."


def list_mcp_servers() -> str:
    """List connected MCP servers and configureable real servers."""
    lines = []
    if mcp_clients:
        lines.append("Connected:")
        for name, c in mcp_clients.items():
            lines.append(f"  {name}: {len(c.tools)} tools")
    else:
        lines.append("No MCP servers connected.")
    if MCP_CONFIG:
        lines.append("\nAvailable real servers (not connected):")
        for name in MCP_CONFIG:
            if name not in mcp_clients:
                entry = MCP_CONFIG[name]
                if isinstance(entry, list):
                    lines.append(f"  {name}: {' '.join(entry)}")
                else:
                    cmd = " ".join([entry.get("command", "")] + entry.get("args", []))
                    lines.append(f"  {name}: {cmd}")
    if MOCK_SERVERS:
        lines.append(f"\nAvailable mock servers: {', '.join(MOCK_SERVERS)}")
    return "\n".join(lines)


def assemble_tool_pool(builtin_handlers: dict) -> tuple[list[dict], dict]:
    """Merge builtin tools + all connected MCP tools into one pool.
    Accepts builtin_handlers via dependency injection to avoid circular imports."""
    tools = list(BUILTIN_TOOLS)
    handlers = dict(builtin_handlers)
    for server_name, mcp_client in mcp_clients.items():
        safe_server = normalize_mcp_name(server_name)
        for tool_def in mcp_client.tools:
            safe_tool = normalize_mcp_name(tool_def.get("name", "unknown"))
            prefixed = f"mcp__{safe_server}__{safe_tool}"
            tools.append({
                "name": prefixed,
                "description": tool_def.get("description", ""),
                "input_schema": tool_def.get("inputSchema", {}),
            })
            handlers[prefixed] = (
                lambda *, c=mcp_client, t=tool_def.get("name", ""), **kw: c.call_tool(t, kw))
    return tools, handlers


# ── Cleanup on exit ──
import atexit as _atexit
def _cleanup_all():
    for name in list(mcp_clients.keys()):
        try:
            mcp_clients[name].close()
        except Exception:
            pass
_atexit.register(_cleanup_all)
