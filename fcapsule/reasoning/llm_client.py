"""LLM boundary for optional model comparison."""

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
    temperature: float = 0.0
    reasoning_effort: str | None = None
    json_output: bool = False


class DeepSeekChatClient:
    """Small OpenAI-compatible DeepSeek client implemented with the standard library."""

    provider = "deepseek"

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
        if request.reasoning_effort is not None:
            body["reasoning_effort"] = request.reasoning_effort
        if request.json_output:
            body["response_format"] = {"type": "json_object"}
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


class OpenRouterChatClient:
    """OpenRouter chat-completions client for the bounded core investigator."""

    provider = "openrouter"

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "OPENROUTER_API_KEY",
        base_url: str = "https://openrouter.ai/api/v1/chat/completions",
        timeout_seconds: int = 120,
    ) -> None:
        load_env_file()
        self.api_key_env = api_key_env
        self.api_key = api_key or os.environ.get(api_key_env)
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        if not self.api_key:
            raise LLMUnavailableError(f"Missing API key. Set {api_key_env} before running the investigation.")

    def chat(self, request: ChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "stream": False,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_output:
            body["response_format"] = {"type": "json_object"}
            body["provider"] = {"require_parameters": True}
        if request.reasoning_effort is not None:
            body["reasoning_effort"] = request.reasoning_effort
        http_request = urllib.request.Request(
            self.base_url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/Hi-io/fcapsule",
                "X-Title": "FCAPSule",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                try:
                    payload = json.loads(response.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise LLMUnavailableError("OpenRouter API returned invalid JSON") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            detail = detail.replace(self.api_key, "[redacted]")
            raise LLMUnavailableError(f"OpenRouter API returned HTTP {exc.code}: {detail}") from exc
        except OSError as exc:
            raise LLMUnavailableError(f"OpenRouter API request failed: {exc}") from exc
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMUnavailableError("OpenRouter API returned no chat choice")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        usage = payload.get("usage", {})
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        content = message.get("content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
        return {
            "model": payload.get("model") or request.model,
            "provider": self.provider,
            "content": content if isinstance(content, str) else "",
            "reasoning_content_present": bool(reasoning),
            "reasoning_content_characters": len(reasoning),
            "finish_reason": choice.get("finish_reason"),
            "usage": usage if isinstance(usage, dict) else {},
            "latency_seconds": round(time.perf_counter() - started, 4),
            "api_response_id": payload.get("id"),
        }
