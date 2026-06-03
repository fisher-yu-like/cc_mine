### 本篇记录openai  functioncall的调用方法

```python
#!/usr/bin/env python3
# Harness: the loop -- the model's first connection to the real world.
"""
s01_agent_loop_openai.py - The Agent Loop (OpenAI Edition)
"""

import os
import subprocess
import json  # OpenAI 解析工具参数需要用到 json


from openai import OpenAI  # 1. 替换为 OpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

# 2. 初始化 OpenAI 客户端
client = OpenAI(
    base_url=os.getenv("LLM_BASE_URL"), # 如果用官方接口可不填，用中转或本地服务（如 vLLM/Ollama）时必填
    api_key=os.getenv("LLM_API_KEY")
)
MODEL = os.getenv("LLM_MODEL_ID")

SYSTEM = f"You are a coding agent at {os.getcwd()}. Use bash to solve tasks. Act, don't explain."

# 3. 转换工具格式 (OpenAI 规范)
TOOLS = [{
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command.",
        "parameters": {  # 从 input_schema 改为 parameters
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }
}]


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


# -- 核心部分：针对 OpenAI 修改的 Agent Loop --
def agent_loop(messages: list):
    while True:
        # 组装 OpenAI 规范的请求，把 system 放到 messages 列表中
        api_messages = [{"role": "system", "content": SYSTEM}] + messages

        response = client.chat.completions.create(
            model=MODEL,
            messages=api_messages,
            tools=TOOLS,
            max_tokens=4000,
        )

        choice = response.choices[0]
        message = choice.message

        # 将模型本轮的回复（包含它想说的话和它想调用的工具）存入历史
        # 注意：OpenAI 要求即便 content 为 None，只要有 tool_calls 也要一同传入
        messages.append(message)

        # 4. 判断终止条件 (OpenAI 的完成原因是 tool_calls)
        if choice.finish_reason != "tool_calls":
            return

        # 5. 执行工具并追加结果
        if message.tool_calls:
            for tool_call in message.tool_calls:
                if tool_call.function.name == "bash":
                    # OpenAI 返回的参数是字符串形式的 JSON，需要手动解析
                    try:
                        args = json.loads(tool_call.function.arguments)
                        command = args.get("command", "")
                    except Exception as e:
                        command = f"echo 'Error parsing arguments: {e}'"

                    print(f"\033[33m$ {command}\033[0m")
                    output = run_bash(command)
                    print(output[:200])

                    # OpenAI 的工具返回格式：role 必须为 "tool"，且带上 tool_call_id
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": "bash",
                        "content": output,
                    })


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)

        # 打印模型的最终文本回复
        last_message = history[-1]
        # 如果最后一条消息是字典对象（用户或工具返回），则向上找模型的最终文本回复
        if isinstance(last_message, dict) and last_message.get("role") == "tool":
            # 找到最后一次模型不是调用工具时的文本输出
            for msg in reversed(history):
                # 检查是否是 ChatCompletionMessage 对象，且有文本内容
                if not isinstance(msg, dict) and getattr(msg, "content", None):
                    print(msg.content)
                    break
        elif hasattr(last_message, "content") and last_message.content:
            print(last_message.content)

        print()
```
