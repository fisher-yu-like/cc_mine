"""
Planning mode for cc_mine.
State machine: IDLE → PLANNING → PLAN_READY → (user approves) → PLAN_APPROVED
When PLANNING: write tools are blocked, agent explores and designs.
"""

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path


class PlanState:
    IDLE = "idle"
    PLANNING = "planning"
    PLAN_READY = "plan_ready"
    PLAN_APPROVED = "plan_approved"
    PLAN_REJECTED = "plan_rejected"


@dataclass
class PlanStep:
    index: int
    description: str
    status: str = "pending"  # pending | in_progress | completed | skipped
    subagent_id: str = ""


@dataclass
class Plan:
    goal: str = ""
    steps: list[dict] = field(default_factory=list)
    state: str = PlanState.IDLE
    created_at: str = ""
    plan_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# Global singleton
_current_plan: Plan = Plan()
_plans_dir: Path | None = None


def init_planning(workdir: Path):
    global _plans_dir
    _plans_dir = workdir / ".cc_mine" / "plans"
    _plans_dir.mkdir(parents=True, exist_ok=True)


def get_state() -> str:
    return _current_plan.state


def get_plan() -> Plan:
    return _current_plan


# ── Tools ──
def enter_plan_mode(goal: str) -> str:
    """Enter planning mode. Write tools will be blocked until plan is approved."""
    global _current_plan
    _current_plan = Plan(
        goal=goal,
        state=PlanState.PLANNING,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        plan_id=f"plan_{int(time.time())}",
    )
    print(f"\n  \033[33m═══ PLAN MODE ═══\033[0m")
    print(f"  Goal: {goal}")
    print(f"  Write tools BLOCKED. Explore, research, design — then call submit_plan.\n")
    return (
        f"Planning mode activated. Goal: {goal}\n"
        "You are now in READ-ONLY mode. Use read_file, glob, web_search to explore. "
        "Use todo_write to outline your plan. When ready, call submit_plan with your plan text and steps."
    )


# Tools that are READ-ONLY — allowed during planning
_READ_ONLY_TOOLS = {
    "read_file", "glob", "web_search", "web_fetch",
    "todo_write", "list_tasks", "get_task",
    "load_skill", "compact", "check_inbox",
    "list_crons", "search_memory", "structured_output",
    "connect_mcp",
}


def is_tool_allowed(tool_name: str) -> bool:
    """Check if a tool is allowed in current plan state."""
    if _current_plan.state != PlanState.PLANNING:
        return True
    return tool_name in _READ_ONLY_TOOLS


def submit_plan(plan_text: str, steps: list[dict]) -> str:
    """Submit a plan for user approval. steps is a list of {description: str} dicts."""
    global _current_plan
    if _current_plan.state != PlanState.PLANNING:
        return "Error: not in planning mode"

    _current_plan.steps = [
        {"index": i, "description": s.get("description", str(s)), "status": "pending"}
        for i, s in enumerate(steps)
    ]
    _current_plan.state = PlanState.PLAN_READY

    # Save to disk
    if _plans_dir:
        path = _plans_dir / f"{_current_plan.plan_id}.json"
        path.write_text(json.dumps(_current_plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    # Display to user
    print(f"\n  \033[33m═══ PLAN SUBMITTED ═══\033[0m")
    print(f"  {plan_text[:200]}")
    print(f"\n  Steps ({len(steps)}):")
    for s in steps:
        print(f"    {s.get('index', '?')+1 if isinstance(s.get('index'), int) else '?'}. {s.get('description', str(s))[:100]}")
    print(f"\n  Type 'y' to approve, 'n' to reject, or give feedback.")

    # Wait for user input
    choice = input("  \033[33mApprove? [y/N/feedback] \033[0m").strip().lower()
    if choice in ("y", "yes", "approve"):
        _current_plan.state = PlanState.PLAN_APPROVED
        print(f"  \033[32m[plan] APPROVED — executing\033[0m\n")
        return f"Plan APPROVED. Exit planning mode and begin execution. Step 1: {steps[0].get('description', steps[0]) if steps else 'none'}"
    elif choice in ("n", "no", "reject", ""):
        _current_plan.state = PlanState.PLAN_REJECTED
        print(f"  \033[31m[plan] REJECTED\033[0m\n")
        return "Plan REJECTED. Revise and resubmit, or call exit_plan_mode to abort."
    else:
        # Feedback
        _current_plan.state = PlanState.PLANNING  # back to planning for revision
        print(f"  \033[33m[plan] feedback received, continue planning\033[0m\n")
        return f"Feedback: {choice}\nRevise your plan and call submit_plan again."


def exit_plan_mode(reason: str = "") -> str:
    """Exit planning mode, returning to normal operation."""
    global _current_plan
    _current_plan.state = PlanState.IDLE
    msg = f"Planning mode exited. {reason}" if reason else "Planning mode exited."
    print(f"  \033[90m[plan] {msg}\033[0m\n")
    return msg


def update_plan_step(step_index: int, status: str) -> str:
    """Update a plan step's status during execution."""
    if step_index < 0 or step_index >= len(_current_plan.steps):
        return f"Invalid step index: {step_index}"
    _current_plan.steps[step_index]["status"] = status
    if _plans_dir and _current_plan.plan_id:
        path = _plans_dir / f"{_current_plan.plan_id}.json"
        path.write_text(json.dumps(_current_plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return f"Step {step_index} → {status}"
