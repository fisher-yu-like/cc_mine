"""
Persistent skill context management.

Skill content is stored OUTSIDE the message history so it survives
context compaction. Content lives in the system prompt (rebuilt fresh
each LLM call), never in messages (which get snipped/compacted).

Usage:
    from skill_context import store_skill_content, get_loaded_skills_context
"""

import threading

# In-memory persistent store: skill_name -> content
_loaded_skills: dict[str, str] = {}
_skills_lock = threading.Lock()

# Max chars per skill in system prompt to prevent context explosion
SKILL_MAX_CHARS = 1500


def store_skill_content(name: str, content: str):
    """Store skill content persistently. Survives context compaction."""
    with _skills_lock:
        _loaded_skills[name] = content


def get_loaded_skills_context() -> str:
    """Return compact context block for all loaded skills.

    Used by assemble_system_prompt to inject into system prompt.
    Each skill is capped at SKILL_MAX_CHARS to prevent explosion.
    """
    with _skills_lock:
        if not _loaded_skills:
            return ""

    parts = ["## Active Skills (always available)"]
    for name, content in _loaded_skills.items():
        if len(content) > SKILL_MAX_CHARS:
            summary = (content[:SKILL_MAX_CHARS] +
                       f"\n... (skill '{name}' truncated, "
                       f"original {len(content)} chars)")
        else:
            summary = content
        parts.append(f"### {name}\n{summary}")

    return "\n\n".join(parts)


def is_skill_loaded(name: str) -> bool:
    with _skills_lock:
        return name in _loaded_skills


def clear_skills():
    with _skills_lock:
        _loaded_skills.clear()


def list_loaded_skills() -> list[str]:
    with _skills_lock:
        return list(_loaded_skills.keys())
