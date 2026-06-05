"""
Tool registry: contains BUILTIN_TOOLS (pure data) and call_tool_handler (simple dispatch).

Layer 1 (Atomic): ~18 core tools — high-frequency, orthogonal, irreplaceable.
Layer 2 (Sandbox): everything else via `bash` — git, cron, worktree, etc.
Layer 3 (Code):   `python` tool — execute multi-step logic in ONE call.
"""


def call_tool_handler(handler, args: dict, name: str) -> str:
    """Simple dispatch: call the handler with args, or return an error message."""
    if not handler:
        return f"Unknown: {name}"
    try:
        return handler(**(args or {}))
    except TypeError as e:
        return f"Error: {e}"


BUILTIN_TOOLS = [
    # ── File Operations ──
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command. This is the primary tool for git, testing, file ops, package management, and any CLI program. Prefer combining multiple commands with && or ; to reduce roundtrips.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "run_in_background": {"type": "boolean", "description": "Run in background for long tasks"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents with optional offset and line limit.",
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
            "description": "Write (create or overwrite) content to a file.",
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
            "description": "Replace exact text in a file once. Prefer this over write_file for small changes — it shows a diff.",
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
    # ── Search ──
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern (e.g. '**/*.py', 'src/**/*.ts').",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Directory to search (default: cwd)"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with a regex pattern. Returns file:line:content matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "File/directory to search"},
                    "glob": {"type": "string", "description": "Glob filter (e.g. '*.py')"},
                    "ignore_case": {"type": "boolean"},
                    "max_results": {"type": "integer"}
                },
                "required": ["pattern"]
            }
        }
    },
    # ── Web ──
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web. Returns titles, URLs, snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "allowed_domains": {"type": "array", "items": {"type": "string"}},
                    "blocked_domains": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and extract readable text content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "prompt": {"type": "string", "description": "Optional: what to extract from the page"}
                },
                "required": ["url"]
            }
        }
    },
    # ── Delegation ──
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": "Spawn a ONE-SHOT worker subagent to execute a specific job. The subagent has: bash, read_file, write_file, edit_file, glob. It returns a text summary. Set run_in_background=true for parallel subagents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What the subagent should do"},
                    "run_in_background": {"type": "boolean", "description": "Run async (for parallel subagents)"}
                },
                "required": ["description"]
            }
        }
    },
    # ── Task Board ──
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a persistent task card on the task board. Use for tracking work across multiple turns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Short task title"},
                    "description": {"type": "string", "description": "Detailed description"},
                    "blockedBy": {"type": "array", "items": {"type": "string"}, "description": "Task IDs this depends on"}
                },
                "required": ["subject"]
            }
        }
    },
    # ── Planning & Progress ──
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Create and update a task list for the current session. Use ONE item in_progress at a time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                                "activeForm": {"type": "string"}
                            },
                            "required": ["content", "status"]
                        }
                    }
                },
                "required": ["todos"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "enter_plan_mode",
            "description": "Enter READ-ONLY planning mode for complex tasks. Explore code, design, then submit_plan for user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "What to plan for"}
                },
                "required": ["goal"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "submit_plan",
            "description": "Submit your plan as a markdown file for user review/editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_text": {"type": "string", "description": "Plan summary"},
                    "steps": {"type": "array", "items": {"type": "object", "properties": {"description": {"type": "string"}}}},
                    "details": {"type": "string", "description": "Optional implementation details"}
                },
                "required": ["plan_text", "steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "exit_plan_mode",
            "description": "Exit planning mode and return to normal operation.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": []
            }
        }
    },
    # ── Context Management ──
    {
        "type": "function",
        "function": {
            "name": "compact",
            "description": "Summarize earlier conversation to free context space.",
            "parameters": {
                "type": "object",
                "properties": {"focus": {"type": "string"}},
                "required": []
            }
        }
    },
    # ── Memory ──
    {
        "type": "function",
        "function": {
            "name": "add_memory",
            "description": "Save a fact to persistent memory (title, content, optional tags).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "tags": {"type": "string", "description": "Comma-separated tags"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search memory files for a query string.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
    },
    # ── External Tools ──
    {
        "type": "function",
        "function": {
            "name": "connect_mcp",
            "description": "Connect to an MCP server to access its tools (e.g. docs, deploy).",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"]
            }
        }
    },
    # ── Layer 3: Python Code Execution ──
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Execute a Python script in ONE call. Use for multi-step logic: loops, data processing, batch file ops, API calls. Avoids N roundtrips for sequential operations. Script runs in the project directory with access to all installed packages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "Python source code to execute"},
                    "timeout": {"type": "integer", "description": "Max seconds (default 30)"}
                },
                "required": ["script"]
            }
        }
    },
]
