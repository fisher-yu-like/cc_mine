import yaml

from config import SKILLS_DIR

SKILL_REGISTRY:dict[str,dict]={}#注册的skill有哪些
def _parse_frontmatter(text:str)->tuple[dict,str]:
    if not text.startswith("---"):#处理SKILL.md的文头，可以增加自动生成文头变成skill
        return {},text
    parts=text.split("---",2)
    if len(parts)<3:
        return {},text
    try:
        meta=yaml.safe_load(parts[1])or {}
    except yaml.YAMLError:
        meta={}
    return meta,parts[2].strip()
'''这个给出一个文头的样式
---
name: "agent-builder"
description: "设计与实现具备明确边界控制、结构化、有状态的多智能体（Multi-Agent）或单智能体系统。"
---
'''
'''
我们知道文件夹格式是这样
skills/xxx/SKILL.md
'''
def scan_skills():
    SKILL_REGISTRY.clear()
    if not SKILLS_DIR.exists():
        return
    for  directory in sorted(SKILLS_DIR.iterdir()):
        if not directory.is_dir():
            continue
        manifest=directory/"SKILL.md"
        if not  manifest.exists():
            continue
        try:
            raw = manifest.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = manifest.read_text(encoding="gbk", errors="replace")
        meta,_=_parse_frontmatter(raw)
        name=meta.get("name",directory.name)
        desc=meta.get("description", raw.split("\n")[0].lstrip("#").strip())
        SKILL_REGISTRY[name]={
            "name":name,
            "description":desc,
            "content":raw
        }

def list_skills()->str:
    if not SKILL_REGISTRY:
        return "(no skills found)"
    return "\n".join(
        f"- {skill['name']}: {skill['description']}"
        for skill in SKILL_REGISTRY.values())

def load_skill(name: str) -> str:
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        available = ", ".join(SKILL_REGISTRY.keys()) or "(none)"
        return f"Skill not found: {name}. Available: {available}"
    return skill["content"]
