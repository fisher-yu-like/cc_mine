import task

_STATUS_ICONS = {
    "completed":   "\033[32m[x]\033[0m",       # green
    "in_progress": "\033[36m[>]\033[0m",       # cyan
    "pending":     "\033[90m[ ]\033[0m",       # gray
}

def run_todo_write(todos: list) -> str:
    for i, todo in enumerate(todos):
        if "content" not in todo or "status" not in todo:
            return f"Error: todos[{i}] missing 'content' or 'status'"
        if todo["status"] not in ("pending", "in_progress", "completed"):
            return f"Error: todos[{i}] has invalid status '{todo['status']}'"
    task.CURRENT_TODOS = todos

    # ── 可视化输出给用户 ──
    print(f"\n  \033[33m--- TODO ---\033[0m")
    for i, t in enumerate(todos, 1):
        icon = _STATUS_ICONS.get(t["status"], "?")
        content = t["content"]
        # 对已完成项用删除线风格（灰色）
        if t["status"] == "completed":
            print(f"  {icon} \033[90m{content}\033[0m")
        elif t["status"] == "in_progress":
            print(f"  {icon} \033[1m{content}\033[0m")  # bold
        else:
            print(f"  {icon} {content}")
    print()

    return f"Updated {len(todos)} todos"
