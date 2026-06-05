from openai_client import OpenAI
from dotenv import  load_dotenv
from  ErrorRecovery import   RecoveryState,with_retry
from  config import  DEFAULT_MAX_TOKENS,PROMPT_SECTIONS
import os
load_dotenv()

# ── Provider management ──
_original_api_key = os.getenv("LLM_API_KEY")
_original_base_url = os.getenv("LLM_BASE_URL")
_original_model = os.getenv("PRIMARY_MODEL", "deepseek-v4-pro")
_provider = "cloud"  # "cloud" | "ollama"

_client = None
FALLBACK_MODEL=os.getenv("FALLBACK_MODEL_ID")


def get_client() -> OpenAI:
    """Return the CURRENT OpenAI client. Lazy-init on first call.

    Modules that import `client` at load time will hold a stale reference when the provider
    is switched (Ollama <-> cloud). This function always returns the live client.
    """
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=_original_api_key,
            base_url=_original_base_url,
        )
    return _client


# ── Provider switching ──
def get_provider_info() -> dict:
    """Returns current provider, model, and base URL."""
    return {
        "provider": _provider,
        "model": os.environ.get("PRIMARY_MODEL", _original_model),
        "base_url": os.environ.get("LLM_BASE_URL", str(_original_base_url or "default")),
        "cloud_model": _original_model,
        "cloud_url": str(_original_base_url or "default"),
    }


def switch_to_ollama(model_name: str) -> str:
    """Switch the OpenAI client to a local Ollama endpoint."""
    global _client, _provider
    os.environ["LLM_API_KEY"] = "ollama"
    os.environ["LLM_BASE_URL"] = "http://localhost:11434/v1"
    os.environ["PRIMARY_MODEL"] = model_name
    _client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
    _provider = "ollama"
    # Sync the cached module-level variable in ErrorRecovery
    _sync_error_recovery_model(model_name)
    print(f"  \033[32m[provider] switched to Ollama → {model_name}\033[0m")
    return f"Switched to Ollama model: {model_name}"


def switch_to_cloud() -> str:
    """Switch back to the original cloud API provider."""
    global _client, _provider
    os.environ["LLM_API_KEY"] = _original_api_key or ""
    os.environ["LLM_BASE_URL"] = _original_base_url or ""
    os.environ["PRIMARY_MODEL"] = _original_model
    _client = OpenAI(
        api_key=_original_api_key or "sk-placeholder",
        base_url=_original_base_url or "",
    )
    _provider = "cloud"
    # Sync the cached module-level variable in ErrorRecovery
    _sync_error_recovery_model(_original_model)
    print(f"  \033[32m[provider] switched back to cloud → {_original_model}\033[0m")
    return f"Switched back to cloud model: {_original_model} ({_original_base_url})"


def _sync_error_recovery_model(model_name: str):
    """Update ErrorRecovery's cached PRIMARY_MODEL so RecoveryState picks up the change."""
    try:
        import ErrorRecovery as _er
        _er.PRIMARY_MODEL = model_name
    except ImportError:
        pass


def list_ollama_models() -> list[str]:
    """Query the Ollama API for available models. Returns empty list if not reachable."""
    import urllib.request as _ur
    import json as _json
    try:
        req = _ur.Request("http://localhost:11434/api/tags")
        with _ur.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        models = [m["name"] for m in data.get("models", [])]
        # Sort: non-latest first, then by name
        models.sort(key=lambda n: (":latest" in n, n))
        return models
    except Exception:
        return []
from datetime import datetime
from skill_load import  list_skills
import mcp

import json
import hashlib

# ── 会话级 token 计数器 ──
_session_tokens = 0
_call_count = 0
_cache_hits = 0
_cache_misses = 0

def get_session_usage() -> tuple[int, int]:
    """Returns (total_estimated_tokens, call_count) for the current session."""
    return _session_tokens, _call_count

def get_cache_stats() -> tuple[int, int]:
    """Returns (cache_hits, cache_misses) for monitoring."""
    return _cache_hits, _cache_misses

def estimate_tokens(messages: list) -> int:
    """Rough token count: CJK chars ~1 tok, ASCII ~0.25 tok. Good enough for budgeting."""
    import re
    text = ""
    for m in messages:
        if isinstance(m, dict):
            text += json.dumps(m.get("content", ""), ensure_ascii=False, default=str)
        else:
            text += str(m)
    cjk = len(re.findall(r'[一-鿿　-〿＀-￯]', text))
    other = len(text) - cjk
    return int(cjk + other * 0.25)


# ── System prompt cache (3-layer) ──
_static_cache = ""
_static_hash = ""
_semi_cache = ""
_semi_hash = ""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def assemble_system_prompt(context: dict) -> str:
    """Return ONLY the stable identity prompt — no dynamic content.

    Claude Code pattern: system prompt is shared across all users for global
    prefix caching. Dynamic content (CC_MINE.md, time, memories, skills)
    goes into messages, NOT here. This keeps the system prompt tiny (~300 tok)
    and cacheable.
    """
    global _static_cache, _static_hash, _cache_hits
    static_raw = PROMPT_SECTIONS["identity"]
    static_h = _hash(static_raw)
    if static_h != _static_hash:
        _static_cache = static_raw
        _static_hash = static_h
    else:
        _cache_hits += 1
    return _static_cache


def build_context_reminder(context: dict) -> str:
    """Build dynamic context block injected into messages (NOT system prompt).

    All session-varying content goes here so the system prompt prefix stays
    stable for caching. Based on Claude Code's <system-reminder> pattern.
    """
    parts = []

    # Time
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts.append(f"<system-reminder>Current time: {current_time}")

    # CC_MINE.md user preferences
    try:
        from config import load_cc_mine_md
        cc_md = load_cc_mine_md()
        if cc_md:
            parts.append(f"<system-reminder>User preferences:\n{cc_md[:1500]}")
    except (ImportError, AttributeError):
        pass

    # Skills catalog (semi-stable — changes only on skill install)
    parts.append(f"<system-reminder>Skills: {list_skills()}")
    try:
        from skill_context import get_loaded_skills_context
        loaded = get_loaded_skills_context()
        if loaded:
            parts.append(f"<system-reminder>Active skills:\n{loaded[:800]}")
    except ImportError:
        pass

    # MCP servers
    mcp_names = list(mcp.mcp_clients.keys())
    if mcp_names:
        parts.append(
            f"<system-reminder>MCP servers: {', '.join(mcp_names)}")

    # Memories
    if context.get("memories"):
        parts.append(f"<system-reminder>Memories:\n{context['memories'][:1500]}")

    return "\n\n".join(parts)
def call_llm_structured(prompt: str, schema: dict, state: RecoveryState,
                        strict: bool = True) -> str:
    """Call LLM with JSON schema constraint, with graceful fallback."""
    from ErrorRecovery import with_retry
    import json as _json

    schema_str = _json.dumps(schema, ensure_ascii=False)
    full_messages = [
        {"role": "system", "content": (
            "You are a precise JSON generator. Output ONLY valid JSON.\n"
            f"Schema: {schema_str}\n"
            "Wrap your JSON output in ```json ... ``` markers."
        )},
        {"role": "user", "content": prompt},
    ]

    # Try native json_schema first
    try:
        return with_retry(
            lambda: get_client().chat.completions.create(
                model=state.current_model,
                messages=full_messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "output",
                        "schema": schema,
                        "strict": strict,
                    }
                },
                max_tokens=4000,
            ), state=state
        ).choices[0].message.content
    except Exception as e:
        err_msg = str(e).lower()
        if "response_format" in err_msg or "json_schema" in err_msg:
            print(f"  \033[33m[structured] json_schema not supported by model, falling back to prompt-based JSON\033[0m")
            # Fallback: regular chat with JSON-in-prompt instructions
            return with_retry(
                lambda: get_client().chat.completions.create(
                    model=state.current_model,
                    messages=full_messages,
                    max_tokens=4000,
                ), state=state
            ).choices[0].message.content
        raise


def call_llm(messages:list,context:dict,tools:list,state:RecoveryState,max_tokens:int):
    # Tiny stable system prompt (identity only) — prefix-cache friendly
    system_prompt = assemble_system_prompt(context)

    # Dynamic context injected into the LAST user message, NOT as a prefix.
    # DeepSeek prefix cache: [system + conversation] is stable; only the
    # final user message (with appended reminder) varies. This maximizes
    # the cached prefix length — every conversation turn hits cache.
    reminder = build_context_reminder(context)
    full_messages = [{"role": "system", "content": system_prompt}]

    if reminder and messages:
        # Inject reminder into the last user message to preserve prefix
        msg_list = [dict(m) for m in messages]  # shallow copy
        for i in range(len(msg_list) - 1, -1, -1):
            if msg_list[i].get("role") == "user":
                old = msg_list[i].get("content", "")
                msg_list[i]["content"] = reminder + "\n\n" + (old or "")
                break
        else:
            msg_list.insert(0, {"role": "user", "content": reminder})
        full_messages.extend(msg_list)
    else:
        full_messages.extend(messages)

    global _session_tokens, _call_count
    est_tokens = estimate_tokens(full_messages)
    _session_tokens += est_tokens
    _call_count += 1

    from terminal_renderer import render_info
    render_info(f"[llm #{_call_count}] ~{est_tokens} tok (session: ~{_session_tokens} tok) | "
                f"{len(full_messages)} msgs | {state.current_model}")
    from log_setup import debug
    debug(f"[llm #{_call_count}] ~{est_tokens} tok, session ~{_session_tokens} tok, "
          f"{len(full_messages)} msgs, model={state.current_model}")

    from spinner import set_state, SpinnerState
    set_state(SpinnerState.CALLING_LLM)
    try:
        return with_retry(
            lambda : get_client().chat.completions.create(
                    model=state.current_model,
                    messages=full_messages,
                    tools=tools if tools else None ,
                    max_tokens=DEFAULT_MAX_TOKENS,
            ),state=state
        )
    finally:
        set_state(SpinnerState.IDLE)