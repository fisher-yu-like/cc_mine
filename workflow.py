"""
Workflow orchestration engine for cc_mine.
DAG-based parallel task execution using async subagents.
Patterns: sequential, parallel (fan-out), phase (map-reduce), diamond.
"""

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from collections import deque

from subagent import spawn_subagent_async, collect_subagent_results as _collect_results
from log_setup import get_logger

log = get_logger()


@dataclass
class WorkflowNode:
    id: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | running | completed | failed
    subagent_id: str = ""
    result: str = ""


@dataclass
class Workflow:
    id: str
    name: str
    description: str = ""
    nodes: dict[str, WorkflowNode] = field(default_factory=dict)
    state: str = "building"  # building | sealed | running | completed | failed | cancelled
    max_parallel: int = 5
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))


_workflows: dict[str, Workflow] = {}
_lock = threading.Lock()
_engine_thread: threading.Thread | None = None
_stop_engine = False


def _start_engine():
    """Start the background workflow engine if not already running."""
    global _engine_thread, _stop_engine
    if _engine_thread is not None and _engine_thread.is_alive():
        return
    _stop_engine = False
    _engine_thread = threading.Thread(target=_engine_loop, daemon=True)
    _engine_thread.start()


def _engine_loop():
    """Background loop: poll running workflows, dispatch ready nodes."""
    while not _stop_engine:
        with _lock:
            workflows = list(_workflows.values())
        for wf in workflows:
            if wf.state == "running":
                _tick_workflow(wf)
        time.sleep(1)


def _tick_workflow(wf: Workflow):
    """Check one workflow: dispatch ready nodes, collect results."""
    # Collect completed subagent results
    sub_results = {}
    for r in _collect_results():
        # Parse subagent result notification
        content = r.get("content", "")
        for line in content.split("\n"):
            if "<id>" in line:
                sid = line.split("<id>")[1].split("</id>")[0].strip()
            if "<summary>" in line:
                summary = line.split("<summary>")[1].split("</summary>")[0].strip()
        if sid:
            sub_results[sid] = summary

    # Apply results to nodes
    for node in wf.nodes.values():
        if node.status == "running" and node.subagent_id in sub_results:
            node.result = sub_results[node.subagent_id]
            node.status = "completed"
            print(f"  \033[32m[workflow {wf.id}] node {node.id} completed\033[0m")

    # Check if all done
    all_done = all(n.status in ("completed", "failed") for n in wf.nodes.values())
    if all_done and wf.state == "running":
        wf.state = "completed"
        print(f"  \033[32m[workflow {wf.id}] COMPLETED — {len(wf.nodes)} nodes\033[0m")
        return

    # Dispatch ready nodes (all deps satisfied, not yet started)
    running = sum(1 for n in wf.nodes.values() if n.status == "running")
    available = wf.max_parallel - running
    if available <= 0:
        return

    for node in wf.nodes.values():
        if node.status != "pending":
            continue
        if available <= 0:
            break
        # Check dependencies
        deps_ready = all(
            wf.nodes.get(dep_id) and wf.nodes[dep_id].status == "completed"
            for dep_id in node.depends_on
        )
        if deps_ready:
            # Build context from dependency results
            context = ""
            if node.depends_on:
                dep_results = [
                    f"[{dep_id}]: {wf.nodes[dep_id].result[:200]}"
                    for dep_id in node.depends_on
                ]
                context = "Previous results:\n" + "\n".join(dep_results) + "\n\n"
            full_desc = context + f"Task: {node.description}"

            sid = spawn_subagent_async(full_desc)
            node.subagent_id = sid
            node.status = "running"
            available -= 1
            print(f"  \033[36m[workflow {wf.id}] node {node.id} dispatched ({sid})\033[0m")


# ── Public API ──
def begin_workflow(name: str, description: str = "") -> str:
    """Create a new workflow. Returns workflow_id."""
    wf_id = f"wf_{int(time.time())}"
    with _lock:
        _workflows[wf_id] = Workflow(id=wf_id, name=name, description=description)
    _start_engine()
    print(f"  \033[33m[workflow] +{wf_id}: {name}\033[0m")
    return wf_id


def add_workflow_node(workflow_id: str, node_id: str,
                      description: str,
                      depends_on: list[str] | None = None) -> str:
    """Add a node to a workflow DAG."""
    with _lock:
        wf = _workflows.get(workflow_id)
        if not wf:
            return f"Error: workflow {workflow_id} not found"
        if wf.state != "building":
            return f"Error: workflow is {wf.state}, cannot add nodes"
        if node_id in wf.nodes:
            return f"Error: node {node_id} already exists"
        wf.nodes[node_id] = WorkflowNode(
            id=node_id, description=description,
            depends_on=depends_on or [],
        )
    return f"Node {node_id} added to {workflow_id}" + (
        f" (depends on: {', '.join(depends_on)})" if depends_on else "")


def seal_workflow(workflow_id: str) -> str:
    """Lock the workflow — no more nodes can be added."""
    with _lock:
        wf = _workflows.get(workflow_id)
        if not wf:
            return f"Error: workflow {workflow_id} not found"
        if wf.state != "building":
            return f"Error: workflow is {wf.state}"
        wf.state = "sealed"
    # Compute stats
    nodes = list(wf.nodes.values())
    has_deps = sum(1 for n in nodes if n.depends_on)
    return (f"Workflow {workflow_id} sealed: {len(nodes)} nodes, "
            f"{has_deps} with dependencies. Call execute_workflow to start.")


def execute_workflow(workflow_id: str, max_parallel: int = 5) -> str:
    """Start executing a sealed workflow."""
    with _lock:
        wf = _workflows.get(workflow_id)
        if not wf:
            return f"Error: workflow {workflow_id} not found"
        if wf.state not in ("sealed",):
            return f"Error: workflow is {wf.state}, must be sealed first"
        wf.state = "running"
        wf.max_parallel = max_parallel
    _start_engine()
    print(f"  \033[33m[workflow {workflow_id}] executing with max {max_parallel} parallel\033[0m")
    return f"Workflow {workflow_id} started. Monitor with workflow_status."


def workflow_status(workflow_id: str) -> str:
    """Get workflow status as text."""
    with _lock:
        wf = _workflows.get(workflow_id)
        if not wf:
            return f"Workflow {workflow_id} not found"
        lines = [
            f"Workflow: {wf.name} ({wf.id}) [{wf.state}]",
            f"Description: {wf.description}" if wf.description else "",
            f"Nodes: {len(wf.nodes)} | Max parallel: {wf.max_parallel}",
            "",
        ]
        for node in wf.nodes.values():
            icon = {"pending": "○", "running": "▶", "completed": "✓", "failed": "✗"}.get(node.status, "?")
            dep_str = f" (depends: {', '.join(node.depends_on)})" if node.depends_on else ""
            lines.append(f"  {icon} {node.id}: {node.description[:80]}{dep_str}")
            if node.result:
                lines.append(f"    → {node.result[:120]}")
        return "\n".join(lines)


def cancel_workflow(workflow_id: str) -> str:
    """Cancel a running workflow."""
    with _lock:
        wf = _workflows.get(workflow_id)
        if not wf:
            return f"Workflow {workflow_id} not found"
        wf.state = "cancelled"
    print(f"  \033[31m[workflow {workflow_id}] cancelled\033[0m")
    return f"Workflow {workflow_id} cancelled"
