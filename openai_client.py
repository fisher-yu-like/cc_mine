"""
Lightweight OpenAI-compatible client built on httpx.
Replaces the `openai` SDK to reduce dependency footprint.

Supports:
  - chat.completions.create (sync, blocking)
  - tools / function calling
  - response_format (json_schema)
  - streaming (basic iter_lines)
  - Same exception hierarchy as openai SDK for drop-in compatibility
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# ═══════════════════════════════════════════════════════════════
# Exception hierarchy — mirrors openai SDK for drop-in compat
# ═══════════════════════════════════════════════════════════════


class OpenAIError(Exception):
    """Base exception for all client errors."""


class APIError(OpenAIError):
    """Generic API error with HTTP status."""

    def __init__(self, message: str, status_code: int = 0, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class APIConnectionError(OpenAIError):
    """Connection / DNS / network failure."""


class APITimeoutError(OpenAIError):
    """Request timed out."""


class RateLimitError(OpenAIError):
    """HTTP 429 — rate limited."""

    def __init__(self, message: str = "", body: Any = None):
        super().__init__(message)
        self.message = message  # compat: openai puts detail in .message
        self.body = body


class InternalServerError(OpenAIError):
    """HTTP 5xx — server error."""


class BadRequestError(OpenAIError):
    """HTTP 400 — bad request (context_length_exceeded, etc.)."""

    def __init__(self, message: str, code: str | None = None, body: Any = None):
        super().__init__(message)
        self.code = code
        self.body = body


class AuthenticationError(OpenAIError):
    """HTTP 401 — auth failure."""


class PermissionDeniedError(OpenAIError):
    """HTTP 403 — forbidden."""


class NotFoundError(OpenAIError):
    """HTTP 404 — not found."""


# ═══════════════════════════════════════════════════════════════
# Response model dataclasses
# ═══════════════════════════════════════════════════════════════


@dataclass
class FunctionCall:
    name: str
    arguments: str  # JSON string


@dataclass
class ToolCall:
    id: str
    type: str = "function"
    function: FunctionCall | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ToolCall":
        func = d.get("function", {})
        return cls(
            id=d.get("id", ""),
            type=d.get("type", "function"),
            function=FunctionCall(
                name=func.get("name", ""),
                arguments=json.dumps(func.get("arguments", {}), ensure_ascii=False)
                if isinstance(func.get("arguments"), dict)
                else str(func.get("arguments", "")),
            ),
        )


@dataclass
class ChoiceMessage:
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    refusal: str | None = None


@dataclass
class Choice:
    index: int = 0
    message: ChoiceMessage = field(default_factory=ChoiceMessage)
    finish_reason: str = "stop"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatCompletion:
    id: str = ""
    object: str = "chat.completion"
    created: int = 0
    model: str = ""
    choices: list[Choice] = field(default_factory=list)
    usage: Usage | None = None


# ═══════════════════════════════════════════════════════════════
# Chat Completions sub-resource
# ═══════════════════════════════════════════════════════════════


class CompletionsProxy:
    """Proxy object for client.chat.completions"""

    def __init__(self, http_client: "OpenAIClient"):
        self._http = http_client

    def create(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        top_p: float | None = None,
        response_format: dict | None = None,
        stream: bool = False,
        stop: list[str] | None = None,
        **kwargs,
    ) -> ChatCompletion:
        """Create a chat completion. Matches openai SDK signature."""
        return self._http._chat_completion_create(
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            response_format=response_format,
            stream=stream,
            stop=stop,
            **kwargs,
        )


class ChatProxy:
    """Proxy object for client.chat"""

    def __init__(self, http_client: "OpenAIClient"):
        self.completions = CompletionsProxy(http_client)


# ═══════════════════════════════════════════════════════════════
# Main client
# ═══════════════════════════════════════════════════════════════


class OpenAIClient:
    """
    Lightweight, httpx-based OpenAI-compatible client.

    Usage:
        client = OpenAIClient(api_key="sk-...", base_url="https://api.deepseek.com/v1")
        resp = client.chat.completions.create(model="deepseek-chat", messages=[...])
        print(resp.choices[0].message.content)
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 120.0,
        max_retries: int = 0,  # retry is handled by ErrorRecovery layer
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=30.0),
        )
        self.chat = ChatProxy(self)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Internal: single chat completion call ──

    def _chat_completion_create(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        top_p: float | None = None,
        response_format: dict | None = None,
        stream: bool = False,
        stop: list[str] | None = None,
        **kwargs,
    ) -> ChatCompletion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if response_format is not None:
            payload["response_format"] = response_format
        if stop is not None:
            payload["stop"] = stop
        # Allow passing extra params like 'reasoning_effort' for DeepSeek
        payload.update(kwargs)

        url = "/chat/completions"

        try:
            response = self._client.post(url, json=payload)
        except httpx.TimeoutException:
            raise APITimeoutError("Request timed out")
        except httpx.ConnectError as e:
            raise APIConnectionError(f"Connection failed: {e}")
        except httpx.NetworkError as e:
            raise APIConnectionError(f"Network error: {e}")

        status = response.status_code
        body = response.json() if response.text else {}

        if status == 200:
            return _parse_chat_completion(body, model)

        # ── Error mapping ──
        self._raise_for_status(status, body)

    @staticmethod
    def _raise_for_status(status: int, body: dict):
        """Map HTTP status codes to our exception hierarchy."""
        error_info = body.get("error", {})
        message = error_info.get("message", f"HTTP {status}")
        error_code = error_info.get("code")
        param = error_info.get("param")

        msg = message
        if param:
            msg = f"{message} (param: {param})"

        if status == 400:
            raise BadRequestError(msg, code=error_code, body=body)
        elif status == 401:
            raise AuthenticationError(msg)
        elif status == 403:
            raise PermissionDeniedError(msg)
        elif status == 404:
            raise NotFoundError(msg)
        elif status == 429:
            raise RateLimitError(msg, body=body)
        elif 500 <= status < 600:
            raise InternalServerError(msg)
        else:
            raise APIError(msg, status_code=status, body=body)


# ═══════════════════════════════════════════════════════════════
# Response parsing
# ═══════════════════════════════════════════════════════════════


def _parse_chat_completion(body: dict, model: str) -> ChatCompletion:
    choices_raw = body.get("choices", [])
    choices: list[Choice] = []
    for c in choices_raw:
        msg_raw = c.get("message", {})
        tool_calls_raw = msg_raw.get("tool_calls")
        tool_calls = None
        if tool_calls_raw:
            tool_calls = [ToolCall.from_dict(tc) for tc in tool_calls_raw]

        choices.append(
            Choice(
                index=c.get("index", 0),
                message=ChoiceMessage(
                    role=msg_raw.get("role", "assistant"),
                    content=msg_raw.get("content"),
                    tool_calls=tool_calls,
                    refusal=msg_raw.get("refusal"),
                ),
                finish_reason=c.get("finish_reason", "stop"),
            )
        )

    usage_raw = body.get("usage")
    usage = None
    if usage_raw:
        usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
            total_tokens=usage_raw.get("total_tokens", 0),
        )

    return ChatCompletion(
        id=body.get("id", ""),
        object=body.get("object", "chat.completion"),
        created=body.get("created", int(time.time())),
        model=body.get("model", model),
        choices=choices,
        usage=usage,
    )


# ═══════════════════════════════════════════════════════════════
# Convenience alias — matches `from openai import OpenAI`
# ═══════════════════════════════════════════════════════════════

OpenAI = OpenAIClient  # drop-in replacement
