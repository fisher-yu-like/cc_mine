#!/usr/bin/env python
"""
cc_mine evaluation runner — inspired by agent-eval methodology.
Runs task definitions from eval/tasks/*.yaml and produces a report.
"""

import json
import subprocess
import sys
import time
import re
import yaml
from pathlib import Path
from datetime import datetime


EVAL_DIR = Path(__file__).parent
TASKS_DIR = EVAL_DIR / "tasks"
RESULTS_DIR = EVAL_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RST = "\033[0m"
BOLD = "\033[1m"


def run_grep_judge(pattern, files_str, expect="true"):
    """Run a grep-based judge. Returns (passed, detail)."""
    files = files_str.split()
    found_files = []
    for f in files:
        p = Path("d:/agent/cc_mine") / f
        if p.exists():
            found_files.append(str(p))

    if not found_files:
        return False, f"No files found (looked for: {files})"

    try:
        # Use Python grep equivalent
        regex = re.compile(pattern.encode() if isinstance(pattern, bytes) else pattern)
        match_count = 0
        matched_files = []
        for fp in found_files:
            try:
                content = Path(fp).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = Path(fp).read_text(encoding="gbk", errors="replace")
            matches = regex.findall(content)
            if matches:
                match_count += len(matches)
                matched_files.append(Path(fp).name)

        if expect == "true":
            passed = match_count > 0
            return passed, f"{match_count} match(es) in {matched_files}"
        else:
            passed = match_count == 0
            return passed, f"{match_count} match(es) (expected 0)"
    except Exception as e:
        return False, f"Grep error: {e}"


def run_python_judge(script):
    """Run a Python script judge. Returns (passed, detail, duration)."""
    start = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd="d:/agent/cc_mine",
            capture_output=True, timeout=30,
        )
        duration = time.perf_counter() - start
        stdout = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()

        if result.returncode == 0:
            return True, stdout or "Python judge passed", duration
        else:
            error_msg = stderr or stdout or f"exit code {result.returncode}"
            # Extract assertion error message
            if "AssertionError" in error_msg:
                lines = error_msg.split("\n")
                error_msg = next((l for l in lines if "AssertionError" in l or "assert" in l), lines[-1])
            return False, error_msg[:200], duration
    except subprocess.TimeoutExpired:
        duration = time.perf_counter() - start
        return False, "Timeout (30s)", duration
    except Exception as e:
        duration = time.perf_counter() - start
        return False, str(e)[:200], duration


def run_task(task_yaml_path):
    """Run a single task YAML. Returns result dict."""
    with open(task_yaml_path, "r", encoding="utf-8") as f:
        task = yaml.safe_load(f)

    task_name = task.get("name", task_yaml_path.stem)
    description = task.get("description", "")
    judges = task.get("judge", [])

    print(f"\n  {CYAN}{BOLD}[{task_name}]{RST}")
    print(f"  {description[:100]}")

    results = []
    all_passed = True
    total_duration = 0.0

    for i, judge in enumerate(judges):
        jtype = judge.get("type", "unknown")

        if jtype == "python":
            passed, detail, duration = run_python_judge(judge["script"])
            total_duration += duration
            icon = f"{GREEN}PASS{RST}" if passed else f"{RED}FAIL{RST}"
            results.append({"type": "python", "passed": passed, "detail": detail, "duration": duration})
            print(f"    judge[{i}] {icon} {detail[:100]} ({duration:.2f}s)")

        elif jtype == "grep":
            passed, detail = run_grep_judge(
                judge["pattern"], judge.get("files", ""), judge.get("expect", "true")
            )
            icon = f"{GREEN}PASS{RST}" if passed else f"{RED}FAIL{RST}"
            results.append({"type": "grep", "passed": passed, "detail": detail})
            print(f"    judge[{i}] {icon} {detail[:100]}")

        elif jtype == "command":
            # Shell command judge
            start = time.perf_counter()
            try:
                r = subprocess.run(
                    judge["command"], shell=True, cwd="d:/agent/cc_mine",
                    capture_output=True, timeout=30,
                )
                duration = time.perf_counter() - start
                passed = r.returncode == 0
                detail = (r.stdout or r.stderr or b"").decode("utf-8", errors="replace")[:200]
                icon = f"{GREEN}PASS{RST}" if passed else f"{RED}FAIL{RST}"
                results.append({"type": "command", "passed": passed, "detail": detail, "duration": duration})
                print(f"    judge[{i}] {icon} {detail[:100]} ({duration:.2f}s)")
            except Exception as e:
                duration = time.perf_counter() - start
                results.append({"type": "command", "passed": False, "detail": str(e)[:200], "duration": duration})
                print(f"    judge[{i}] {RED}FAIL{RST} {str(e)[:100]}")

        if not results[-1].get("passed", False):
            all_passed = False

    pass_count = sum(1 for r in results if r.get("passed"))
    fail_count = len(results) - pass_count
    print(f"  {GREEN if all_passed else RED}{pass_count}/{len(results)} passed{RST}"
          f" ({total_duration:.2f}s total)")

    return {
        "task": task_name,
        "description": description,
        "passed": all_passed,
        "judges_total": len(results),
        "judges_passed": pass_count,
        "judges_failed": fail_count,
        "duration": total_duration,
        "details": results,
    }


def generate_report(task_results, report_path):
    """Generate a Markdown + JSON report."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # JSON
    json_path = report_path.with_suffix(".json")
    json_path.write_text(json.dumps({
        "generated": ts,
        "agent": "cc_mine",
        "tasks": task_results,
        "summary": {
            "total_tasks": len(task_results),
            "passed_tasks": sum(1 for t in task_results if t["passed"]),
            "failed_tasks": sum(1 for t in task_results if not t["passed"]),
            "total_judges": sum(t["judges_total"] for t in task_results),
            "passed_judges": sum(t["judges_passed"] for t in task_results),
            "total_duration": sum(t["duration"] for t in task_results),
        },
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # Markdown
    summary = json.loads(json_path.read_text(encoding="utf-8"))["summary"]
    md = f"""# cc_mine Evaluation Report

**Generated**: {ts}
**Agent**: cc_mine (self-written Claude Code clone)

## Summary

| Metric | Value |
|--------|-------|
| Tasks | {summary['total_tasks']} |
| Passed | {summary['passed_tasks']} |
| Failed | {summary['failed_tasks']} |
| Judges total | {summary['total_judges']} |
| Judges passed | {summary['passed_judges']} |
| Pass rate | {summary['passed_judges']}/{summary['total_judges']} ({summary['passed_judges']*100//max(summary['total_judges'],1)}%) |
| Total duration | {summary['total_duration']:.2f}s |

## Tasks

"""
    for t in task_results:
        icon = "✅" if t["passed"] else "❌"
        md += f"### {icon} {t['task']}\n\n"
        md += f"{t['description']}\n\n"
        md += f"Judges: {t['judges_passed']}/{t['judges_total']} passed ({t['duration']:.2f}s)\n\n"
        for i, d in enumerate(t["details"]):
            p_icon = "✅" if d["passed"] else "❌"
            dur = f" ({d.get('duration', 0):.2f}s)" if "duration" in d else ""
            md += f"  - {p_icon} `{d['type']}`: {d['detail'][:150]}{dur}\n"
        md += "\n"

    report_path.with_suffix(".md").write_text(md, encoding="utf-8")

    return json_path, report_path.with_suffix(".md")


def main():
    print(f"{BOLD}{CYAN}cc_mine Evaluation Runner{RST}")
    print(f"Tasks dir: {TASKS_DIR}")
    print(f"Results dir: {RESULTS_DIR}")

    task_files = sorted(TASKS_DIR.glob("*.yaml"))
    if not task_files:
        print(f"{RED}No task YAML files found in {TASKS_DIR}{RST}")
        sys.exit(1)

    print(f"\nFound {len(task_files)} task(s)\n{'='*60}")

    start = time.perf_counter()
    results = []
    for tf in task_files:
        result = run_task(tf)
        results.append(result)

    total_dur = time.perf_counter() - start
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed

    print(f"\n{'='*60}")
    print(f"{BOLD}Total: {GREEN}{passed} passed{RST}, "
          f"{RED}{failed} failed{RST} out of {len(results)} tasks "
          f"({total_dur:.2f}s){RST}")

    # Save report
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"eval_{ts}"
    json_path, md_path = generate_report(results, report_path)
    print(f"\nReport saved:")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
