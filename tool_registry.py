"""
Tool registry: contains BUILTIN_TOOLS (pure data) and call_tool_handler (simple dispatch).
Extracted from executor.py to break circular imports.

This module has ZERO project-level imports — it is safe to import from anywhere.
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
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "run_in_background": {"type": "boolean"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
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
            "description": "Write content to a file.",
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
            "description": "Replace exact text in a file once.",
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
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern. Use 'pattern' (or 'path' as alias).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Create and manage a task list for the current session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}
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
            "name": "task",
            "description": "Spawn a ONE-SHOT worker subagent. Returns a text summary (sync) or a subagent_id (async). Set run_in_background=true when you want to spawn multiple subagents in parallel — their results arrive as notifications.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "run_in_background": {"type": "boolean"}
                },
                "required": ["description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Load the full content of a skill by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compact",
            "description": "Summarize earlier conversation and continue with compacted context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus": {"type": "string"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                    "blockedBy": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["subject"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List all tasks.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_task",
            "description": "Get full task details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "claim_task",
            "description": "Claim a pending task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Complete an in-progress task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_cron",
            "description": "Schedule a cron job. cron is 5-field: min hour dom month dow. For one-shot reminders, compute the target minute and set recurring=false.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cron": {"type": "string"},
                    "prompt": {"type": "string"},
                    "recurring": {"type": "boolean"},
                    "durable": {"type": "boolean"}
                },
                "required": ["cron", "prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_crons",
            "description": "List registered cron jobs.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_cron",
            "description": "Cancel a cron job by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"}
                },
                "required": ["job_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_teammate",
            "description": "Spawn a PERSISTENT background agent that runs until shutdown. Use for: parallel workers, ongoing roles (reviewer/tester), autonomous task-board workers. For one-shot jobs use task instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "prompt": {"type": "string"}
                },
                "required": ["name", "role", "prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send message to a teammate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["to", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_inbox",
            "description": "Check inbox for messages and protocol responses.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "request_shutdown",
            "description": "Request a teammate to shut down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "teammate": {"type": "string"}
                },
                "required": ["teammate"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "request_plan",
            "description": "Ask a teammate to submit a plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "teammate": {"type": "string"},
                    "task": {"type": "string"}
                },
                "required": ["teammate", "task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "review_plan",
            "description": "Approve or reject a submitted plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "approve": {"type": "boolean"},
                    "feedback": {"type": "string"}
                },
                "required": ["request_id", "approve"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_worktree",
            "description": "Create an isolated git worktree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "task_id": {"type": "string"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_worktree",
            "description": "Remove a worktree. Refuses if changes exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "discard_changes": {"type": "boolean"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keep_worktree",
            "description": "Keep a worktree for manual review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "connect_mcp",
            "description": "Connect to an MCP server (docs, deploy) and discover tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web and return top results with titles, URLs, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
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
                    "url": {"type": "string", "description": "URL to fetch"},
                    "prompt": {"type": "string", "description": "Optional context/prompt for the fetched content"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "begin_workflow",
            "description": "Create a new workflow DAG for orchestrating parallel subagent tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_workflow_node",
            "description": "Add a node to a workflow. Set depends_on for sequential/phase patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "node_id": {"type": "string"},
                    "description": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["workflow_id", "node_id", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "seal_workflow",
            "description": "Lock a workflow — no more nodes can be added.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"}
                },
                "required": ["workflow_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_workflow",
            "description": "Execute a sealed workflow with up to max_parallel concurrent nodes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "max_parallel": {"type": "integer"}
                },
                "required": ["workflow_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "workflow_status",
            "description": "Get status of a workflow including all nodes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"}
                },
                "required": ["workflow_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_workflow",
            "description": "Cancel a running workflow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"}
                },
                "required": ["workflow_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "enter_plan_mode",
            "description": "Enter READ-ONLY planning mode. Explore code, research, design — write tools blocked until plan approved.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "What the plan aims to accomplish"}
                },
                "required": ["goal"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "submit_plan",
            "description": "Submit your plan for user approval. Include plan text and numbered steps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_text": {"type": "string", "description": "Summary of the plan"},
                    "steps": {"type": "array", "items": {"type": "object", "properties": {"description": {"type": "string"}}}}
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
                "properties": {
                    "reason": {"type": "string"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan_step",
            "description": "Update a plan step's status during execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "step_index": {"type": "integer"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "skipped"]}
                },
                "required": ["step_index", "status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "structured_output",
            "description": "Generate a JSON response matching a schema. Use when you need structured data output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Task description"},
                    "schema": {"type": "object", "description": "JSON Schema for the output"},
                    "strict": {"type": "boolean", "description": "Enforce strict schema compliance"}
                },
                "required": ["prompt", "schema"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_memory",
            "description": "Save a fact to persistent memory with title, content, and optional comma-separated tags.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "tags": {"type": "string"}
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
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_memory",
            "description": "Delete a memory by its slug name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"]
            }
        }
    }
]
