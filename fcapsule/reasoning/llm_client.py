"""LLM boundary for optional P1 model comparison."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from fcapsule.env import load_env_file


class LLMClient(Protocol):
    def generate_hypotheses(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


class LLMUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: list[dict[str, str]]
    max_tokens: int = 2400
    temperature: float = 0.2


class DeepSeekChatClient:
    """Small OpenAI-compatible DeepSeek client implemented with the standard library."""

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "DEEPSEEK_API_KEY",
        base_url: str = "https://api.deepseek.com/chat/completions",
        timeout_seconds: int = 120,
    ) -> None:
        load_env_file()
        self.api_key_env = api_key_env
        self.api_key = api_key or os.environ.get(api_key_env)
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        if not self.api_key:
            raise LLMUnavailableError(f"Missing API key. Set {api_key_env} before running model comparison.")

    def chat(self, request: ChatRequest) -> dict[str, Any]:
        body = {
            "model": request.model,
            "messages": request.messages,
            "stream": False,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        http_request = urllib.request.Request(
            self.base_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise LLMUnavailableError(f"DeepSeek API returned HTTP {exc.code}: {detail}") from exc
        except OSError as exc:
            raise LLMUnavailableError(f"DeepSeek API request failed: {exc}") from exc
        choice = payload.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = payload.get("usage", {})
        return {
            "model": request.model,
            "provider": "deepseek",
            "content": message.get("content", ""),
            "reasoning_content_present": bool(message.get("reasoning_content")),
            "reasoning_content_characters": len(message.get("reasoning_content", "")),
            "finish_reason": choice.get("finish_reason"),
            "usage": usage,
            "latency_seconds": round(time.perf_counter() - started, 4),
            "api_response_id": payload.get("id"),
        }
