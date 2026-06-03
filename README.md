# cc_mine

A self-written Claude Code clone powered by OpenAI-compatible LLMs (DeepSeek, OpenAI, etc.).

## Features

- **Agent Loop** — Turn-based LLM interaction with 38 built-in tools
- **Subagent System** — One-shot workers (sync/async) and persistent teammates
- **Planning Mode** — Read-only exploration → plan submission → user approval → execution
- **Workflow Engine** — DAG-based parallel task orchestration (fan-out, map-reduce, pipeline)
- **Task Management** — Persistent task board with dependency graph (blockedBy)
- **Context Compaction** — 4-layer defense: budget → snip → micro → AI summary
- **Structured Memory** — Frontmatter .md memory cards with search
- **Session Persistence** — Auto-save + `--resume`
- **Web Tools** — `web_search` + `web_fetch`
- **Permission Model** — Configurable allow/deny/ask rules with hot-reload
- **MCP Support** — Pluggable external tool servers (mock + real)
- **Git Worktrees** — Isolated workspaces for parallel agents
- **Cron Scheduler** — 5-field cron with durable persistence
- **Error Recovery** — Exponential backoff, model fallback on server errors

## Quick Start

```bash
# 1. Clone
git clone https://github.com/YOUR_USER/cc_mine.git
cd cc_mine

# 2. Install dependencies
pip install openai python-dotenv pyyaml requests

# 3. Configure
cp .env.example .env
# Edit .env with your API key

# 4. Run
python main.py

# Optional: specify workdir and model
python main.py --workdir /path/to/project --model deepseek-v4-pro

# Resume a previous session
python main.py --resume
```

## Architecture

```
cc_mine/
├── main.py              # Agent loop + CLI
├── config.py            # Configuration + system prompts
├── call_llm.py          # LLM API wrapper + token counting
├── executor.py          # Tool handler routing
├── tool_registry.py     # 38 built-in tool definitions
├── hooks.py             # Permission + logging + safety hooks
├── memory.py            # 4-layer context compaction + memory CRUD
├── ErrorRecovery.py     # Exponential backoff + model fallback
├── planning.py          # EnterPlanMode / submit / approve
├── workflow.py          # DAG-based parallel task orchestration
├── session.py           # Session save / load / resume
├── log_setup.py         # Console + file logging
├── subagent.py          # One-shot subagent (+ async)
├── AutonomousAgent.py   # Persistent background teammates
├── task.py              # Task board with dependencies
├── MessageBus.py        # Inter-agent file-based messaging
├── ProtocolState.py     # Shutdown + plan approval handshakes
├── CronScheduler.py     # 5-field cron with durable persistence
├── mcp.py               # MCP server integration
├── bg_task.py           # Background bash execution
├── worktree.py          # Git worktree isolation
├── skill_load.py        # SKILL.md loader
├── skills/              # Pre-packaged skills
├── tools/
│   ├── bash.py          # Shell command execution
│   ├── file_ops.py      # read/write/edit/glob
│   ├── git.py           # Git operations
│   ├── todo_write.py    # Visual todo tracking
│   └── web.py           # Web search + fetch
└── .env.example         # Environment template
```

## License

MIT
