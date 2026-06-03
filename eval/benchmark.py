"""
cc_mine Benchmark Suite
========================

A comprehensive evaluation framework inspired by:
  - SWE-bench: Task → resolution verification
  - agentbench: 5-dimension 0-100 scoring
  - VibeCodingBench: Multi-dimensional composite
  - SWE Atlas: Q&A + modification + refactoring

Dimensions:
  1. Tool Completeness    (20%) — All tools dispatch correctly
  2. Code Understanding   (20%) — Read, search, grep accuracy
  3. Code Modification    (20%) — Edit precision, diff quality
  4. Planning & Execution (15%) — Multi-step task completion
  5. Context & Memory     (10%) — Memory CRUD, compaction survival
  6. Safety & Permissions (10%) — Deny list, path checks
  7. Performance          ( 5%) — Token efficiency, responsiveness

Usage:
    python eval/benchmark.py                    # Run all suites
    python eval/benchmark.py --suite tool       # Run specific suite
    python eval/benchmark.py --suite tool --task task_01  # Single task
    python eval/benchmark.py --list             # List all tasks
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

# ── Project root ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUITES_DIR = Path(__file__).resolve().parent / "suites"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════


@dataclass
class TaskResult:
    task_id: str
    suite: str
    name: str
    passed: bool
    score: float          # 0.0 – 1.0
    duration_ms: float
    details: str = ""
    expected: str = ""
    actual: str = ""
    error: str = ""


@dataclass
class SuiteResult:
    suite: str
    dimension: str
    weight: float
    tasks: list[TaskResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if not self.tasks:
            return 0.0
        return sum(1 for t in self.tasks if t.passed) / len(self.tasks)

    @property
    def avg_score(self) -> float:
        if not self.tasks:
            return 0.0
        return sum(t.score for t in self.tasks) / len(self.tasks)

    @property
    def total_duration_ms(self) -> float:
        return sum(t.duration_ms for t in self.tasks)


# ═══════════════════════════════════════════════════════════════
# Judgment strategies
# ═══════════════════════════════════════════════════════════════


def judge_python(code: str, expected: Any) -> tuple[bool, float, str]:
    """Execute Python code; return (passed, score, output).

    The code should assign to a variable `_result`.
    PROJECT_ROOT is added to sys.path so imports work.
    """
    import sys as _sys
    namespace = {"__builtins__": __builtins__}
    # Ensure project root is on path for imports
    if str(PROJECT_ROOT) not in _sys.path:
        _sys.path.insert(0, str(PROJECT_ROOT))
    try:
        exec(code, namespace)
        result = namespace.get("_result")
        if result == expected:
            return True, 1.0, str(result)
        return False, 0.0, f"got={result!r}, expected={expected!r}"
    except Exception as e:
        return False, 0.0, f"{type(e).__name__}: {e}"


def judge_grep(pattern: str, path: str, expected_count: int = 1) -> tuple[bool, float, str]:
    """Grep for pattern in path; check count >= expected_count.

    Uses Python's re module (cross-platform, no ripgrep dependency).
    """
    import re as _re
    try:
        target = PROJECT_ROOT / path
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = list(target.rglob("*.py")) + list(target.rglob("*.yaml")) + list(target.rglob("*.md"))
        else:
            files = []

        count = 0
        for f in files[:200]:  # limit scanning
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            count += len(_re.findall(pattern, content))

        ok = count >= expected_count
        return ok, (1.0 if ok else 0.0), f"found {count} matches (need >= {expected_count})"
    except Exception as e:
        return False, 0.0, f"grep error: {e}"


def judge_command(command: str, expected_exit: int = 0,
                  expected_contains: str = "") -> tuple[bool, float, str]:
    """Run a shell command and check exit code / output."""
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, cwd=str(PROJECT_ROOT)
        )
        exit_ok = r.returncode == expected_exit
        contains_ok = expected_contains in (r.stdout + r.stderr) if expected_contains else True
        ok = exit_ok and contains_ok
        score = 1.0 if ok else (0.5 if exit_ok else 0.0)
        return ok, score, f"exit={r.returncode}, contains={'OK' if contains_ok else 'FAIL'}"
    except subprocess.TimeoutExpired:
        return False, 0.0, "timeout (30s)"
    except Exception as e:
        return False, 0.0, f"error: {e}"


def judge_file_contains(path: str, text: str) -> tuple[bool, float, str]:
    """Check that a file exists and contains the given text."""
    fp = PROJECT_ROOT / path
    if not fp.exists():
        return False, 0.0, f"file not found: {path}"
    try:
        content = fp.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = fp.read_text(encoding="gbk", errors="replace")
    ok = text in content
    return ok, (1.0 if ok else 0.0), f"file exists, text {'found' if ok else 'NOT found'}"


def judge_file_not_contains(path: str, text: str) -> tuple[bool, float, str]:
    """Check that a file does NOT contain the given text."""
    fp = PROJECT_ROOT / path
    if not fp.exists():
        return True, 1.0, f"file not found: {path} (OK — nothing to check)"
    try:
        content = fp.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = fp.read_text(encoding="gbk", errors="replace")
    ok = text not in content
    return ok, (1.0 if ok else 0.0), f"text {'correctly absent' if ok else 'INCORRECTLY present'}"


JUDGES = {
    "python": judge_python,
    "grep": judge_grep,
    "command": judge_command,
    "file_contains": judge_file_contains,
    "file_not_contains": judge_file_not_contains,
}


# ═══════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════


def load_suite(suite_name: str) -> dict | None:
    """Load a YAML task suite."""
    path = SUITES_DIR / f"{suite_name}.yaml"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_all_tasks() -> list[dict]:
    """List all available tasks across suites."""
    tasks = []
    for yf in sorted(SUITES_DIR.glob("*.yaml")):
        suite = yf.stem
        data = load_suite(suite)
        if not data:
            continue
        for task in data.get("tasks", []):
            tasks.append({"suite": suite, **task})
    return tasks


def run_task(suite: str, task: dict, verbose: bool = False) -> TaskResult:
    """Execute a single benchmark task."""
    task_id = task["id"]
    name = task.get("name", task_id)
    judge_type = task["judge"]
    judge_args = task.get("args", {})
    timeout_ms = task.get("timeout_ms", 15000)

    result = TaskResult(
        task_id=task_id,
        suite=suite,
        name=name,
        passed=False,
        score=0.0,
        duration_ms=0,
    )

    judge_fn = JUDGES.get(judge_type)
    if not judge_fn:
        result.error = f"Unknown judge type: {judge_type}"
        return result

    # Merge task-level expected into args for judge_python
    effective_args = dict(judge_args)
    if "expected" in task and "expected" not in effective_args:
        effective_args["expected"] = task["expected"]

    t0 = time.time()
    try:
        passed, score, output = judge_fn(**effective_args)
        elapsed = (time.time() - t0) * 1000

        result.passed = passed
        result.score = max(0.0, min(1.0, score))
        result.duration_ms = elapsed
        result.actual = str(output)[:500]
        result.expected = task.get("expected", "")

        if verbose:
            status = "\033[32mPASS\033[0m" if passed else "\033[31mFAIL\033[0m"
            print(f"  [{status}] {task_id}: {name} ({elapsed:.0f}ms)")
            if not passed:
                print(f"         expected: {result.expected[:100]}")
                print(f"         actual:   {result.actual[:100]}")
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        result.duration_ms = elapsed
        result.error = f"{type(e).__name__}: {e}"
        if verbose:
            print(f"  [\033[31mERR\033[0m] {task_id}: {name} — {result.error}")

    return result


def run_benchmark(suite_filter: str = "", task_filter: str = "",
                  verbose: bool = True) -> list[SuiteResult]:
    """Run all benchmark suites (or a filtered subset)."""
    all_suite_results = []

    # Find suites
    suites_to_run = []
    if suite_filter:
        s = load_suite(suite_filter)
        if s:
            suites_to_run.append((suite_filter, s))
        else:
            print(f"Suite not found: {suite_filter}")
            return []
    else:
        for yf in sorted(SUITES_DIR.glob("*.yaml")):
            s = load_suite(yf.stem)
            if s:
                suites_to_run.append((yf.stem, s))

    if not suites_to_run:
        print("No suites found.")
        return []

    total_tasks = 0
    total_passed = 0
    total_start = time.time()

    for suite_name, suite_data in suites_to_run:
        suite_dim = suite_data.get("dimension", suite_name)
        suite_weight = suite_data.get("weight", 0.10)
        all_tasks = suite_data.get("tasks", [])

        # Filter tasks
        if task_filter:
            all_tasks = [t for t in all_tasks if t["id"] == task_filter]
            if not all_tasks:
                print(f"Task not found: {task_filter} in suite {suite_name}")
                continue

        if verbose:
            print(f"\n{'='*60}")
            print(f"\033[1m{suite_name}\033[0m — {suite_dim} (weight: {suite_weight*100:.0f}%)")
            print(f"{'='*60}")

        suite_result = SuiteResult(
            suite=suite_name,
            dimension=suite_dim,
            weight=suite_weight,
        )

        for task in all_tasks:
            r = run_task(suite_name, task, verbose=verbose)
            suite_result.tasks.append(r)

        total_tasks += len(suite_result.tasks)
        total_passed += sum(1 for t in suite_result.tasks if t.passed)
        all_suite_results.append(suite_result)

        if verbose:
            pct = suite_result.pass_rate * 100
            print(f"  Suite: {pct:.0f}% passed, avg score: {suite_result.avg_score:.2f}")

    total_elapsed = time.time() - total_start

    # ── Compute composite score ──
    composite = 0.0
    for sr in all_suite_results:
        composite += sr.avg_score * sr.weight

    if verbose and all_suite_results:
        print(f"\n{'='*60}")
        print(f"\033[1;36mBENCHMARK RESULTS\033[0m")
        print(f"{'='*60}")
        for sr in all_suite_results:
            bar = "#" * int(sr.pass_rate * 20) + "-" * (20 - int(sr.pass_rate * 20))
            print(f"  {sr.dimension:30s} [{bar}] {sr.pass_rate*100:5.1f}%  x{sr.weight:.2f}")
        print(f"  {'─'*58}")
        print(f"  {'COMPOSITE SCORE':30s}   {composite*100:5.1f}/100")
        print(f"  Tasks: {total_passed}/{total_tasks} passed, {total_elapsed:.1f}s total")
        print()

    # ── Save report ──
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"benchmark_{ts}.json"
    report = _build_report(all_suite_results, composite, total_elapsed)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if verbose:
        print(f"  Report saved: {report_path}")

    # Also save markdown
    md_path = RESULTS_DIR / f"benchmark_{ts}.md"
    md_path.write_text(_build_markdown(report), encoding="utf-8")
    if verbose:
        print(f"  Markdown:     {md_path}")

    return all_suite_results


def _build_report(suite_results: list[SuiteResult], composite: float,
                  total_seconds: float) -> dict:
    """Build a JSON-serializable report."""
    suites = []
    all_tasks = []
    for sr in suite_results:
        suites.append({
            "suite": sr.suite,
            "dimension": sr.dimension,
            "weight": sr.weight,
            "pass_rate": round(sr.pass_rate, 4),
            "avg_score": round(sr.avg_score, 4),
            "duration_ms": round(sr.total_duration_ms, 0),
            "task_count": len(sr.tasks),
        })
        all_tasks.extend(sr.tasks)

    return {
        "benchmark": "cc_mine",
        "timestamp": datetime.now().isoformat(),
        "composite_score": round(composite * 100, 1),
        "total_seconds": round(total_seconds, 1),
        "total_tasks": len(all_tasks),
        "total_passed": sum(1 for t in all_tasks if t.passed),
        "suites": suites,
        "tasks": [
            {
                "suite": t.suite,
                "id": t.task_id,
                "name": t.name,
                "passed": t.passed,
                "score": t.score,
                "duration_ms": round(t.duration_ms, 0),
                "details": t.details[:200],
                "error": t.error[:200],
            }
            for t in all_tasks
        ],
    }


def _build_markdown(report: dict) -> str:
    """Build a GitHub-flavored Markdown report."""
    lines = [
        f"# cc_mine Benchmark Report",
        f"",
        f"**Date:** {report['timestamp'][:19]}  ",
        f"**Composite Score:** {report['composite_score']:.1f}/100  ",
        f"**Tasks:** {report['total_passed']}/{report['total_tasks']} passed  ",
        f"**Duration:** {report['total_seconds']:.1f}s  ",
        f"",
        f"## Dimension Scores",
        f"",
        f"| Dimension | Weight | Pass Rate | Avg Score | Tasks |",
        f"|-----------|--------|-----------|-----------|-------|",
    ]
    for s in report["suites"]:
        lines.append(
            f"| {s['dimension']} | {s['weight']*100:.0f}% | "
            f"{s['pass_rate']*100:.0f}% | {s['avg_score']:.2f} | {s['task_count']} |"
        )

    lines.extend([
        f"",
        f"## Task Details",
        f"",
        f"| Suite | Task | Name | Pass | Score |",
        f"|-------|------|------|------|-------|",
    ])
    for t in report["tasks"]:
        status = "✅" if t["passed"] else "❌"
        lines.append(
            f"| {t['suite']} | {t['id']} | {t['name']} | {status} | {t['score']:.2f} |"
        )

    lines.extend([
        f"",
        f"---",
        f"*Generated by eval/benchmark.py*",
    ])
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════


def main():
    ap = argparse.ArgumentParser(
        description="cc_mine Benchmark Suite — evaluate coding agent capabilities"
    )
    ap.add_argument("--suite", default="", help="Run a specific suite (e.g. tool, safety)")
    ap.add_argument("--task", default="", help="Run a specific task ID (requires --suite)")
    ap.add_argument("--list", action="store_true", help="List all available tasks")
    ap.add_argument("--verbose", "-v", action="store_true", default=True,
                    help="Verbose output (default)")
    ap.add_argument("--quiet", "-q", action="store_true", help="Quiet output")
    args = ap.parse_args()

    if args.list:
        tasks = list_all_tasks()
        if not tasks:
            print("No tasks found.")
            return
        by_suite = {}
        for t in tasks:
            by_suite.setdefault(t["suite"], []).append(t)
        for suite, tl in sorted(by_suite.items()):
            print(f"\n\033[1m{suite}\033[0m")
            for t in tl:
                print(f"  {t['id']:20s} {t.get('name', '')}")
            print(f"  ({len(tl)} tasks)")
        return

    run_benchmark(
        suite_filter=args.suite,
        task_filter=args.task,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
