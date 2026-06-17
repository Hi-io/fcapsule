"""Future LLM boundary; P1 defaults to deterministic reasoning."""

from __future__ import annotations

from typing import Any, Protocol


class LLMClient(Protocol):
    def generate_hypotheses(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


class LLMUnavailableError(RuntimeError):
    pass
