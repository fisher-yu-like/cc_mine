import os
import random
import time
from openai import RateLimitError, InternalServerError, APITimeoutError, APIConnectionError, BadRequestError
from dotenv import load_dotenv
from config import BASE_DELAY_MS, MAX_RETRIES, MAX_CONSECUTIVE_529

load_dotenv()
PRIMARY_MODEL=os.getenv("PRIMARY_MODEL")
FALLBACK_MODEL=os.getenv("FALLBACK_MODEL_ID")
class RecoveryState:
    def __init__(self):
        self.has_escalated=False
        self.recovery_count=0
        self.consecutive_529=0
        self.has_attempted_reactive_compact=False
        self.current_model=PRIMARY_MODEL

def retry_delay(attempt:int)->float:
    base=min(BASE_DELAY_MS*(2**attempt),32000)/1000
    return base+random.uniform(0,base*0.25)


def with_retry(fn, state: RecoveryState):
    for attempt in range(int(MAX_RETRIES)):
        try:
            result = fn()
            state.consecutive_529 = 0  # 成功后重置连续错误计数器
            return result

        except RateLimitError as e:
            # 1. 精准捕获 OpenAI 的 429 速率限制错误
            delay = retry_delay(attempt)
            print(f"  \033[33m[OpenAI 429] Rate limit hit. Retry {attempt + 1}/{MAX_RETRIES} "
                  f"after {delay:.1f}s. Detail: {e.message}\033[0m")
            time.sleep(delay)
            continue

        except (InternalServerError, APITimeoutError, APIConnectionError) as e:
            # 2. 捕获 OpenAI 的服务器崩溃(500/503)、超时以及连接中断错误
            state.consecutive_529 += 1

            # 如果连续服务器错误达到阈值，触发动态大脑降级（降级到本地开源模型或轻量模型）
            if state.consecutive_529 >= int(MAX_CONSECUTIVE_529) and FALLBACK_MODEL:
                state.current_model = FALLBACK_MODEL
                state.consecutive_529 = 0
                print(f"  \033[31m[OpenAI Server Error] Switching brain to fallback model: {FALLBACK_MODEL}\033[0m")

            delay = retry_delay(attempt)
            print(f"  \033[33m[OpenAI Error] Server issue ({type(e).__name__}). "
                  f"Retry {attempt + 1}/{MAX_RETRIES} after {delay:.1f}s\033[0m")
            time.sleep(delay)
            continue

        except Exception as e:
            # 3. 如果是其他错误（比如参数传错 BadRequestError、认证失败 AuthenticationError）
            # 绝对不应该盲目重试，必须直接抛出让上层感知
            raise

    raise RuntimeError(f"Max retries ({MAX_RETRIES}) exceeded")


def is_prompt_too_long_error(e: Exception) -> bool:
    # 1. 首先检查是否为 OpenAI 的请求错误 (HTTP 400)
    if isinstance(e, BadRequestError):
        # 2. 检查具体的错误代码是否为上下文超限
        # OpenAI 的错误体中通常包含 'code': 'context_length_exceeded'
        if e.code == "context_length_exceeded":
            return True

    # 3. 兜底逻辑：如果使用了某些代理转发或者非官方库，保留字符串检查作为 fallback
    msg = str(e).lower()
    return (("prompt" in msg and "long" in msg)
            or "context_length_exceeded" in msg
            or "max_context_window" in msg)