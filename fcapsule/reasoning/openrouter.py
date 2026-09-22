"""Narrow OpenRouter boundary for optional image and speech evidence.

The client deliberately exposes only the two provider operations FCAPSule needs:
bounded visual extraction and short audio transcription. It never stores a key,
and callers are responsible for applying capability and cost gates first.
"""

from __future__ import annotations

import base64
import io
import json
import os
import struct
import time
import urllib.error
import urllib.request
import wave
import zlib
from typing import Any

from fcapsule.env import load_env_file


class OpenRouterError(RuntimeError):
    """A provider failure whose text is safe to show as an operational status."""


def _canary_png() -> bytes:
    """Build a valid 32px RGB image accepted by stricter vision providers."""

    width = height = 32
    raw = b"".join(b"\x00" + b"\x2f\x6f\x5f" * width for _ in range(height))

    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


_CANARY_PNG = _canary_png()
_AUDIO_FORMATS = {
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
}


class OpenRouterClient:
    """Standard-library OpenRouter client with bounded request payloads."""

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "OPENROUTER_API_KEY",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: int = 90,
    ) -> None:
        load_env_file()
        self.api_key = api_key or os.environ.get(api_key_env)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        if not self.api_key:
            raise OpenRouterError(f"Missing API key. Set {api_key_env} before enabling media evidence.")

    def _request(self, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise OpenRouterError(f"OpenRouter API returned HTTP {exc.code}: {detail}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise OpenRouterError(f"OpenRouter API request failed: {exc}") from exc
        if not isinstance(body, dict):
            raise OpenRouterError("OpenRouter returned an unexpected response shape")
        return body, round(time.perf_counter() - started, 4)

    @staticmethod
    def _content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterError("OpenRouter response did not include a model choice")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict)).strip()
            if text:
                return text
        raise OpenRouterError("OpenRouter response did not include usable model content")

    @staticmethod
    def _usage(payload: dict[str, Any]) -> dict[str, Any]:
        usage = payload.get("usage")
        return usage if isinstance(usage, dict) else {}

    def visual_extract(self, image: bytes, mime_type: str, model: str, max_tokens: int = 800) -> dict[str, Any]:
        if len(image) > 6 * 1024 * 1024:
            raise OpenRouterError("Image evidence exceeds the 6 MiB provider upload limit")
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise OpenRouterError("Unsupported image format; use PNG, JPEG, or WebP")
        encoded = base64.b64encode(image).decode("ascii")
        prompt = (
            "Extract only visible operational facts from this screenshot. Return concise JSON with keys "
            "visible_text (array), observations (array of {fact, confidence, region}), ambiguities (array), "
            "and limitation (string). Preserve exact identifiers where readable. Do not infer unseen targets, "
            "timestamps, configuration, or a diagnosis. Return one JSON object only, with no Markdown or prose."
        )
        payload, latency = self._request(
            "/chat/completions",
            {
                "model": model,
                "temperature": 0,
                "max_tokens": max(128, min(800, int(max_tokens))),
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
                ]}],
            },
        )
        return {
            "provider": "openrouter",
            "model": model,
            "content": self._content(payload),
            "usage": self._usage(payload),
            "latency_seconds": latency,
            "response_id": payload.get("id"),
        }

    def transcribe(self, audio: bytes, mime_type: str, model: str, language: str | None = None) -> dict[str, Any]:
        if len(audio) > 8 * 1024 * 1024:
            raise OpenRouterError("Audio evidence exceeds the 8 MiB provider upload limit")
        if mime_type not in {"audio/wav", "audio/mpeg", "audio/ogg", "audio/webm", "audio/mp4", "audio/x-m4a"}:
            raise OpenRouterError("Unsupported audio format; use WAV, MP3, OGG, WebM, or M4A")
        request: dict[str, Any] = {
            "model": model,
            "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": _AUDIO_FORMATS[mime_type]},
            # Plain JSON is supported by more transcription providers than the
            # optional timestamp-rich format. Segments are supplementary evidence,
            # not a precondition for an operator-provided transcript.
            "response_format": "json",
        }
        if language in {"en", "es"}:
            request["language"] = language
        payload, latency = self._request("/audio/transcriptions", request)
        transcript = payload.get("text") or payload.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip():
            raise OpenRouterError("OpenRouter transcription response did not include text")
        return {
            "provider": "openrouter",
            "model": model,
            "transcript": transcript.strip(),
            "segments": payload.get("segments") if isinstance(payload.get("segments"), list) else [],
            "usage": self._usage(payload),
            "latency_seconds": latency,
            "response_id": payload.get("id"),
        }

    def validate_vision(self, model: str) -> dict[str, Any]:
        response = self.visual_extract(_CANARY_PNG, "image/png", model, max_tokens=128)
        return {"model": model, "usage": response["usage"], "latency_seconds": response["latency_seconds"]}

    def validate_asr(self, model: str) -> dict[str, Any]:
        """Verify native audio access with a tiny non-sensitive silent WAV canary."""

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(8000)
            writer.writeframes(b"\x00\x00" * 800)
        payload, latency = self._request(
            "/audio/transcriptions",
            {
                "model": model,
                "input_audio": {
                    "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                    "format": "wav",
                },
                "response_format": "json",
            },
        )
        # Silence can legitimately yield an empty transcript. A successful typed
        # response still proves this credential/model accepts audio requests.
        return {"model": model, "usage": self._usage(payload), "latency_seconds": latency}
