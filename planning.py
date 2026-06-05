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
    # Directory already created by config.ensure_directories()
    if not _plans_dir.exists():
        _plans_dir.mkdir(parents=True, exist_ok=True)


_approved_turns: int = 0


def get_state() -> str:
    """Return current plan state. Auto-exits if agent stalls after approval."""
    global _approved_turns
    if _current_plan.state == PlanState.PLAN_APPROVED:
        _approved_turns += 1
        if _approved_turns >= 3:
            # Agent hasn't called exit_plan_mode after 3 turns — force exit
            exit_plan_mode("auto-exit after approval (agent stalled)")
            return PlanState.IDLE
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
# submit_plan and exit_plan_mode MUST be here so the agent can always
# submit or abort plan mode (even during PLANNING state).
_READ_ONLY_TOOLS = {
    "read_file", "glob", "grep",
    "web_search", "web_fetch",
    "todo_write", "list_tasks", "get_task",
    "load_skill", "compact", "check_inbox",
    "list_crons", "search_memory", "structured_output",
    "connect_mcp", "list_mcp_servers", "workflow_status",
    "submit_plan", "exit_plan_mode",
}


def is_tool_allowed(tool_name: str) -> bool:
    """Check if a tool is allowed in current plan state."""
    if _current_plan.state != PlanState.PLANNING:
        return True
    return tool_name in _READ_ONLY_TOOLS


def _render_plan_markdown(plan: Plan, details: str = "") -> str:
    """Render a Plan into a user-editable markdown file."""
    goal = plan.goal or "(no goal specified)"
    created = plan.created_at or time.strftime("%Y-%m-%dT%H:%M:%S")
    steps_md = ""
    for i, s in enumerate(plan.steps):
        desc = s.get("description", str(s))
        status = s.get("status", "pending")
        checkbox = " " if status in ("pending", "in_progress") else "x"
        steps_md += f"{i+1}. [{checkbox}] {desc}\n"

    details_section = ""
    if details.strip():
        details_section = f"""
---

## Implementation Details

{details}
"""

    return f"""# Plan: {goal}

| Field | Value |
|-------|-------|
| **Plan ID** | {plan.plan_id} |
| **Created** | {created} |
| **Status** | ⏳ Awaiting Approval |

---

## Goal

{goal}

{details_section}
---

## Steps
<!-- Edit these steps as needed — add, remove, reorder, or modify descriptions. -->
<!-- The system will re-read this file when you approve, capturing your edits. -->
<!-- Check off completed steps: change [ ] to [x] -->

{steps_md}
---

## Notes
<!-- Add any notes, concerns, or modifications here -->

"""


def _parse_plan_markdown(text: str) -> dict:
    """
    Parse user-edited plan markdown back into structured data.
    Returns {"goal": str, "steps": list[dict]}.
    Steps are extracted from numbered list items under ## Steps.
    """
    import re
    result = {"goal": "", "steps": []}

    # Extract goal from ## Goal section
    goal_match = re.search(r"##\s*Goal\s*\n+(.*?)(?:\n##|\n---|\Z)", text, re.DOTALL)
    if goal_match:
        result["goal"] = goal_match.group(1).strip()

    # Extract steps from ## Steps section
    steps_match = re.search(r"##\s*Steps\s*\n+(.*?)(?:\n##|\n---|\Z)", text, re.DOTALL)
    if steps_match:
        steps_text = steps_match.group(1)
        # Match lines like "1. [ ] description" or "1. description" or "1) description"
        step_lines = re.findall(r"^\d+[.)]\s*(?:\[.\]\s*)?(.+)$", steps_text, re.MULTILINE)
        for i, desc in enumerate(step_lines):
            desc = desc.strip()
            if desc:
                # Check if marked as complete
                status_match = re.match(rf"^{i+1}[.)]\s*\[(.)\]\s*(.+)$", steps_text, re.MULTILINE)
                status = "pending"
                result["steps"].append({"index": i, "description": desc, "status": status})

    return result


def submit_plan(plan_text: str, steps: list[dict], details: str = "") -> str:
    """Submit a plan for user approval. Writes plan to a markdown file for user editing.
    The user can open and edit the .md file. On /plan-approve, the file is re-read
    to capture user modifications.

    Args:
        plan_text: Summary of the plan / goal
        steps: List of {"description": "..."} dicts
        details: Optional detailed implementation notes (rendered in markdown)
    """
    global _current_plan
    if _current_plan.state != PlanState.PLANNING:
        return "Error: not in planning mode"

    _current_plan.steps = [
        {"index": i, "description": s.get("description", str(s)), "status": "pending"}
        for i, s in enumerate(steps)
    ]
    _current_plan.state = PlanState.PLAN_READY

    # Save JSON (internal state)
    md_path = None
    if _plans_dir:
        json_path = _plans_dir / f"{_current_plan.plan_id}.json"
        json_path.write_text(json.dumps(_current_plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

        # Generate markdown plan file for user to read and edit in IDE
        md_content = _render_plan_markdown(_current_plan, details)
        md_path = _plans_dir / f"{_current_plan.plan_id}.md"
        md_path.write_text(md_content, encoding="utf-8")

    # Display to user — no blocking input()
    sep = "\033[33m" + "=" * 60 + "\033[0m"
    print(f"\n  {sep}")
    print(f"  \033[33m  [PLAN] SUBMITTED -- awaiting your approval\033[0m")
    print(f"  {sep}")
    print(f"  Goal: {plan_text[:200]}")
    if md_path:
        print(f"\n  \033[36m  [file] Plan file:\033[0m {md_path}")
        print(f"  \033[36m        -> Open this file in your IDE to review and edit\033[0m")
    print(f"\n  Steps ({len(steps)}):")
    for i, s in enumerate(steps):
        print(f"    {i+1}. {s.get('description', str(s))[:100]}")
    print(f"\n  \033[32m  [OK] /plan-approve  -- approve & execute (reads your edits)\033[0m")
    print(f"  \033[31m  [X] /plan-reject [feedback]  -- reject or request revision\033[0m")
    print(f"  \033[90m  [i] Tip: Edit the .md file before approving to customize the plan\033[0m")
    print(f"  {sep}\n")

    return (
        f"Plan submitted and saved as {_current_plan.plan_id}. "
        f"A markdown plan file has been written for the user to review and edit. "
        f"WAIT for user approval. Do NOT proceed until you receive '[Plan Approved]' message. "
        f"The user will approve via /plan-approve or reject via /plan-reject. "
        f"When the user approves, the system will re-read the plan file to capture any edits."
    )


def approve_plan() -> str:
    """Called by CLI /plan-approve. Re-reads the plan markdown file to capture
    any user edits, then injects approval into the conversation."""
    global _current_plan, _approved_turns
    if _current_plan.state != PlanState.PLAN_READY:
        return f"No plan awaiting approval (current state: {_current_plan.state})"

    # ── Re-read the markdown plan file to capture user edits ──
    changes_detected = False
    if _plans_dir:
        md_path = _plans_dir / f"{_current_plan.plan_id}.md"
        if md_path.exists():
            user_text = md_path.read_text(encoding="utf-8")
            user_plan = _parse_plan_markdown(user_text)

            # Detect user modifications
            if user_plan["goal"] and user_plan["goal"] != _current_plan.goal:
                old_goal = _current_plan.goal
                _current_plan.goal = user_plan["goal"]
                print(f"  \033[36m[plan] Goal updated by user:\033[0m")
                print(f"         old: {old_goal[:80]}")
                print(f"         new: {user_plan['goal'][:80]}")
                changes_detected = True

            if user_plan["steps"]:
                old_count = len(_current_plan.steps)
                new_count = len(user_plan["steps"])
                if old_count != new_count:
                    print(f"  \033[36m[plan] Steps changed: {old_count} → {new_count}\033[0m")
                    changes_detected = True
                else:
                    # Check if descriptions changed
                    for i, (old_s, new_s) in enumerate(zip(_current_plan.steps, user_plan["steps"])):
                        if old_s.get("description") != new_s.get("description"):
                            print(f"  \033[36m[plan] Step {i+1} edited by user\033[0m")
                            changes_detected = True

                _current_plan.steps = user_plan["steps"]

            if not changes_detected:
                print(f"  \033[90m[plan] No user edits detected in plan file\033[0m")

            # Save updated JSON with user edits
            json_path = _plans_dir / f"{_current_plan.plan_id}.json"
            json_path.write_text(json.dumps(_current_plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    _current_plan.state = PlanState.PLAN_APPROVED
    _approved_turns = 0  # reset counter — if agent doesn't exit within 3 turns, force it
    print(f"  \033[32m[plan] APPROVED — executing\033[0m")
    if changes_detected:
        print(f"  \033[32m[plan] User edits have been incorporated\033[0m")
    print()

    # Build first-step hint
    step_hint = ""
    if _current_plan.steps:
        first = _current_plan.steps[0].get("description", "none")
        step_hint = f"\nFirst step: {first}"

    return (
        "[Plan Approved]\n"
        "Your plan has been approved by the user. "
        + ("The plan file has been updated with the user's edits. " if changes_detected else "")
        + "You MUST call exit_plan_mode NOW to leave planning mode. "
        + "This is REQUIRED before you can use write tools. Do NOT call any other tool first — "
        + "call exit_plan_mode immediately, then begin executing the steps."
        + step_hint
    )


def reject_plan(feedback: str = "") -> str:
    """Called by CLI /plan-reject. Injects rejection/feedback into conversation."""
    global _current_plan
    if _current_plan.state != PlanState.PLAN_READY:
        return f"No plan awaiting approval (current state: {_current_plan.state})"

    if feedback.strip():
        _current_plan.state = PlanState.PLANNING  # back to planning for revision
        print(f"  \033[33m[plan] feedback received, back to planning\033[0m\n")
        return (
            f"[Plan Feedback]\n"
            f"The user reviewed your plan and gave feedback: \"{feedback}\"\n"
            f"Revise your plan and call submit_plan again."
        )
    else:
        _current_plan.state = PlanState.PLAN_REJECTED
        print(f"  \033[31m[plan] REJECTED\033[0m\n")
        return (
            "[Plan Rejected]\n"
            "Your plan was rejected by the user. "
            "Revise and resubmit, or call exit_plan_mode to abort."
        )


def exit_plan_mode(reason: str = "") -> str:
    """Exit planning mode, returning to normal operation."""
    global _current_plan, _approved_turns
    _current_plan.state = PlanState.IDLE
    _approved_turns = 0
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
