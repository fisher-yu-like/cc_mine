from openai import  OpenAI
from dotenv import  load_dotenv
from  ErrorRecovery import   RecoveryState,with_retry
from  config import  DEFAULT_MAX_TOKENS,PROMPT_SECTIONS
import os
load_dotenv()
client= OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
)
FALLBACK_MODEL=os.getenv("FALLBACK_MODEL_ID")
from datetime import datetime
from skill_load import  list_skills
import mcp

import json

# ── 会话级 token 计数器 ──
_session_tokens = 0
_call_count = 0

def get_session_usage() -> tuple[int, int]:
    """Returns (total_estimated_tokens, call_count) for the current session."""
    return _session_tokens, _call_count

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


def assemble_system_prompt(context: dict) -> str:
    # 1. 基础模块（移除了纯文本的 tools 描述，让 OpenAI 原生 tools 参数去搞定）
    sections = [
        PROMPT_SECTIONS["identity"],
        PROMPT_SECTIONS["workspace"]
    ]

    # 2. 注入动态时间（这对写代码、判断日志时效至关重要）
    # 格式化为更符合人类习惯的：2026-06-01 19:28:15
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections.append(f"Current UTC/Local time: {current_time_str}")

    # 3. 动态注入技能树（按需加载，防止 Context 爆炸）
    sections.append("Skills catalog:\n" + list_skills() +
                    "\nUse load_skill(name) when a skill is relevant.")

    # 4. 注入长期记忆 / 用户偏好
    if context.get("memories"):
        sections.append(f"Relevant memories:\n{context['memories']}")

    # 5. MCP 协议状态注入
    mcp_names = list(mcp.mcp_clients.keys())
    if mcp_names:
        sections.append(f"Connected MCP servers: {', '.join(mcp_names)}")

    # 6. 用双换行符隔开，形成清晰的 Markdown 级联文档
    return "\n\n".join(sections)
def call_llm_structured(prompt: str, schema: dict, state: RecoveryState,
                        strict: bool = True) -> str:
    """Call LLM with JSON schema constraint. Returns the raw JSON string."""
    from ErrorRecovery import with_retry
    full_messages = [
        {"role": "system", "content": "You are a precise JSON generator. Output ONLY valid JSON matching the schema."},
        {"role": "user", "content": prompt},
    ]
    return with_retry(
        lambda: client.chat.completions.create(
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


def call_llm(messages:list,context:dict,tools:list,state:RecoveryState,max_tokens:int):
    #每次调用大模型需要组装新的提示词
    system_prompt = assemble_system_prompt(context)
    #openai结构需要把系统提示词放在首位，因此每次需要重新构造message
    full_messages=[{"role": "system", "content": system_prompt}]+messages

    global _session_tokens, _call_count
    est_tokens = estimate_tokens(full_messages)
    _session_tokens += est_tokens
    _call_count += 1
    print(f"  \033[90m[llm #{_call_count}] ~{est_tokens} tok (session: ~{_session_tokens} tok) | "
          f"{len(full_messages)} msgs | {state.current_model}\033[0m")
    from log_setup import debug
    debug(f"[llm #{_call_count}] ~{est_tokens} tok, session ~{_session_tokens} tok, "
          f"{len(full_messages)} msgs, model={state.current_model}")

    return with_retry(
        lambda : client.chat.completions.create(
                model=state.current_model,
                messages=full_messages,
                tools=tools if tools else None ,
                max_tokens=DEFAULT_MAX_TOKENS,
        ),state=state
    )