import json
import time
from config import MAILBOX_DIR
import config

if not MAILBOX_DIR.exists():
    MAILBOX_DIR.mkdir(exist_ok=True)
import threading
import os


def terminal_print(text: str):
    """Print text above the prompt area. Thread-safe.

    When running inside prompt_toolkit, uses its print_formatted_text
    to avoid corrupting the input area. Falls back to plain print().
    """
    if threading.current_thread() is threading.main_thread() or not config.CLI_ACTIVE:
        print(text)
        return
    # Use prompt_toolkit's thread-safe print mechanism
    try:
        from prompt_toolkit_input import terminal_print_above
        terminal_print_above(text)
    except ImportError:
        print(f"\r\033[K{text}")

class MessageBus:
    def send(self,from_agent:str,to_agent:str,content:str,msg_type:str="message",metadata:dict=None):
        msg={"from":from_agent,"to":to_agent,"content":content,"type":msg_type,"ts":time.time(),"metadata":metadata or {}}
        inbox  =MAILBOX_DIR/f"{to_agent}.jsonl"
        with open(inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg)+"\n")
        terminal_print(f"  \033[33m[bus] {from_agent} → {to_agent}: "
                       f"({msg_type}) {content[:50]}\033[0m")

    def read_inbox(self,agent:str)->list[dict]:
        inbox=MAILBOX_DIR/f"{agent}.jsonl"
        if not inbox.exists():
            return []
        # 建立一个临时的“处理中”信箱
        processing_inbox = MAILBOX_DIR / f"{agent}.processing_{time.time()}.jsonl"

        try:
            # 利用操作系统的底层原子操作：直接重命名文件。
            # 此时如果有新消息发送，会自动写入全新的 {agent}.jsonl 中，绝不会丢失。
            os.rename(inbox, processing_inbox)

            msgs = [json.loads(line) for line in processing_inbox.read_text(encoding="utf-8").splitlines() if line.strip()]
            processing_inbox.unlink()  # 安全地删除临时文件
            return msgs
        except FileNotFoundError:
            return []

BUS = MessageBus()
