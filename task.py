"""
Task
"""


import json
import random
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from config import  TASKS_DIR
if not TASKS_DIR.exists():
    TASKS_DIR.mkdir(exist_ok=True)
CURRENT_TODOS: list[dict] = []

@dataclass
class  Task:
    id:str
    subject:str
    description:str=""
    status:str="pending"
    owner:str|None=None
    blockedBy:list[str]=None
    worktree:str|None=None

    def __post_init__(self):
        if self.blockedBy is None:
            self.blockedBy = []
'''其中blockedBy起到“任务依赖看板的作用，可以理解为一个有向图'''
def _task_path(task_id:str)->Path:
    return TASKS_DIR/f"{task_id}.json"
def create_task(subject:str,description:str="",blockedBy:list[str]|None=None)->Task:
    task=Task(
        id=f"task_{int(time.time())}_{random.randint(0,9999):04d}",
        subject=subject,description=description,
        status="pending",owner=None,
        blockedBy=blockedBy or []
    )
    save_task(task)
    return task

def  save_task(task:Task):
    _task_path(task.id).write_text(json.dumps(asdict(task), indent=2), encoding="utf-8")

def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text(encoding="utf-8")))
#先读取信息，再loads转换为python字典，然后根据dataclass中的**转化为Task对象

def list_tasks() -> list[Task]:
    return [Task(**json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(TASKS_DIR.glob("task_*.json"))]


def get_task_json(task_id: str) -> str:
    return json.dumps(asdict(load_task(task_id)), indent=2)

def  can_start(task_id:str)->bool:
    #检查前置任务是否存在，是否完成
    task=load_task(task_id)
    for dep_id in task.blockedBy:
        if not  _task_path(dep_id).exists():
            return False
        if load_task(dep_id).status!="completed":
            return False
    return True
def claim_task(task_id:str,owner:str="agent")->str:
    #分配任务
    task=load_task(task_id)
    if task.status !="pending":
        return f"Task {task_id} is {task.status}, cannot claim"
    if task.owner:
        return f"Task {task_id} already owned by {task.owner}"
    if not can_start(task_id):
        deps=[d for d in task.blockedBy
              if _task_path(d).exists()and load_task(d).status!="completed"]
        missing=[d for d in task.blockedBy if not _task_path(d)]
        parts=[]
        if deps:parts.append(f"blocked by: {deps}")
        if missing:parts.append(f"missing deps: {missing}")
        return "Cannot start — " + ", ".join(parts)
    task.owner=owner
    task.status="in_progress"
    save_task(task)
    print(f"  \033[36m[claim] {task.subject} → in_progress\033[0m")
    return f"Claimed {task.id} ({task.subject})"
def complete_task(task_id:str)->str:
    task=load_task(task_id)
    if task.status!="in_progress":
        return f"Task {task_id} is {task.status}, cannot complete"
    task.status="completed"
    save_task(task)
    unblocked=[t.subject for t in list_tasks() if t.status=="pending"and t.blockedBy and can_start(t.id)]
    print(f"  \033[32m[complete] {task.subject} [OK]\033[0m")
    msg = f"Completed {task.id} ({task.subject})"
    if unblocked:
        msg += f"\nUnblocked: {', '.join(unblocked)}"
    return msg
