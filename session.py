"""
Session persistence for cc_mine.
Auto-saves history + context after each turn. Supports --resume on startup.
"""

import json
import time
from pathlib import Path
from datetime import datetime

SESSION_EXT = ".jsonl"
META_EXT = ".meta.json"


def _session_dir(workdir: Path) -> Path:
    d = workdir / ".cc_mine" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_path(workdir: Path, session_id: str) -> Path:
    return _session_dir(workdir) / f"{session_id}{SESSION_EXT}"


def _meta_path(workdir: Path, session_id: str) -> Path:
    return _session_dir(workdir) / f"{session_id}{META_EXT}"


def save_session(history: list, context: dict, workdir: Path,
                 session_id: str | None = None,
                 label: str = "") -> str:
    """Save current session to disk. Returns session_id."""
    if session_id is None:
        session_id = f"session_{int(time.time())}"

    # Save message history as JSONL
    path = _session_path(workdir, session_id)
    with path.open("w", encoding="utf-8") as f:
        for msg in history:
            f.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")

    # Save metadata
    meta_path = _meta_path(workdir, session_id)
    meta_path.write_text(json.dumps({
        "session_id": session_id,
        "label": label or f"Session {session_id}",
        "created": datetime.now().isoformat(),
        "message_count": len(history),
        "tools_connected": list(context.get("connected_mcp", [])),
        "active_teammates": list(context.get("active_teammates", [])),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    return session_id


def load_session(workdir: Path, session_id: str) -> tuple[list, dict] | None:
    """Load a saved session. Returns (history, context) or None."""
    path = _session_path(workdir, session_id)
    meta_path = _meta_path(workdir, session_id)

    if not path.exists():
        return None

    history = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                history.append(json.loads(line))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"  \033[31m[session] corrupted: {e}\033[0m")
        return None

    context = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            context = {
                "memories": "",
                "connected_mcp": meta.get("tools_connected", []),
                "active_teammates": meta.get("active_teammates", []),
            }
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    return history, context


def list_sessions(workdir: Path) -> list[dict]:
    """List all saved sessions."""
    d = _session_dir(workdir)
    sessions = []
    for meta_file in sorted(d.glob(f"*{META_EXT}"), reverse=True):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            sessions.append(meta)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return sessions


def latest_session(workdir: Path) -> str | None:
    """Return the most recent session_id, or None."""
    sessions = list_sessions(workdir)
    return sessions[0]["session_id"] if sessions else None


def delete_session(workdir: Path, session_id: str) -> str:
    """Delete a session. Returns confirmation."""
    path = _session_path(workdir, session_id)
    meta_path = _meta_path(workdir, session_id)
    removed = 0
    for p in (path, meta_path):
        if p.exists():
            p.unlink()
            removed += 1
    return f"Deleted {removed} file(s) for session {session_id}"
