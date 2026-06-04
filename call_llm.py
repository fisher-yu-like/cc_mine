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
    global _static_cache, _static_hash, _semi_cache, _semi_hash, _cache_hits, _cache_misses

    sections = []

    # ── Layer A: Static (never changes during session) ──
    # identity + workspace + CC_MINE.md
    cc_mine_md = ""
    try:
        from config import load_cc_mine_md
        cc_mine_md = load_cc_mine_md()
    except (ImportError, AttributeError):
        pass

    static_raw = (PROMPT_SECTIONS["identity"] + "\n\n" +
                  PROMPT_SECTIONS["workspace"] + "\n\n" + cc_mine_md)
    static_h = _hash(static_raw)
    if static_h != _static_hash:
        _static_cache = static_raw
        _static_hash = static_h
    sections.append(_static_cache)

    # ── Layer B: Semi-static (rarely changes) ──
    # tools description + skills catalog + MCP list + loaded skills
    skills_text = ("Skills catalog:\n" + list_skills() +
                   "\nUse load_skill(name) when a skill is relevant.")
    mcp_names = list(mcp.mcp_clients.keys())
    mcp_text = (f"Connected MCP servers: {', '.join(mcp_names)}"
                if mcp_names else "")
    loaded_skills = ""
    try:
        from skill_context import get_loaded_skills_context
        loaded_skills = get_loaded_skills_context()
    except ImportError:
        pass

    semi_raw = (skills_text + "\n\n" + mcp_text + "\n\n" + loaded_skills)
    semi_h = _hash(semi_raw)
    if semi_h != _semi_hash:
        _semi_cache = semi_raw
        _semi_hash = semi_h
        _cache_misses += 1
    else:
        _cache_hits += 1
    sections.append(_semi_cache)

    # ── Layer C: Dynamic (changes every call) ──
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections.append(f"Current UTC/Local time: {current_time_str}")

    if context.get("memories"):
        sections.append(f"Relevant memories:\n{context['memories']}")

    return "\n\n".join(sections)
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
    #每次调用大模型需要组装新的提示词
    system_prompt = assemble_system_prompt(context)
    #openai结构需要把系统提示词放在首位，因此每次需要重新构造message
    full_messages=[{"role": "system", "content": system_prompt}]+messages

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