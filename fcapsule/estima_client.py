"""Small bounded HTTP client for the optional Estima memory service."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


class EstimaClientError(RuntimeError):
    """An Estima request failed; response bodies are deliberately not retained."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_estima_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Estima URL must be an absolute HTTP(S) URL without embedded credentials")
    if parts.scheme == "http":
        host = parts.hostname.lower().rstrip(".")
        if host not in {"localhost", "127.0.0.1", "::1"} and not (host.endswith(".svc") or host.endswith(".svc.cluster.local")):
            raise ValueError("Estima URL must use HTTPS except for localhost or in-cluster .svc DNS")
    return url


def validate_atlas_url(value: str) -> str:
    """Compatibility alias for callers using the previous service name."""
    return validate_estima_url(value)


def estima_settings_from_env(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    def value(name: str, default: str = "") -> str:
        current_name = f"FCAPSULE_COLLECTIVE_{name}"
        new_name = f"FCAPSULE_ESTIMA_{name}"
        old_name = f"FCAPSULE_ATLAS_{name}"
        if current_name in env:
            return str(env[current_name])
        return str(env[new_name] if new_name in env else env.get(old_name, default))

    return {
        "url": value("URL").strip(),
        "token": value("TOKEN"),
        "instance_id": value("INSTANCE_ID").strip(),
        "read_enabled": _enabled(value("READ", "false")),
        "publish_enabled": _enabled(value("PUBLISH", "false")),
    }


class EstimaClient:
    """Bounded client for Estima's stable v1 memory/search/pattern endpoints."""

    def __init__(self, base_url: str, bearer_token: str | None = None, timeout_seconds: float = 2.0) -> None:
        self.base_url = validate_estima_url(base_url)
        self.bearer_token = str(bearer_token or "").strip() or None
        self.timeout_seconds = min(10.0, max(0.2, float(timeout_seconds)))

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url + path
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(512 * 1024 + 1)
        except HTTPError as error:
            raise EstimaClientError(f"Estima returned HTTP {error.code}", status_code=error.code) from None
        except (URLError, TimeoutError, OSError) as error:
            raise EstimaClientError(f"Estima request unavailable ({type(error).__name__})") from None
        if len(body) > 512 * 1024:
            raise EstimaClientError("Estima response exceeded the 512 KiB limit")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EstimaClientError("Estima returned invalid JSON") from None
        if not isinstance(value, dict):
            raise EstimaClientError("Estima returned a non-object response")
        return value

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/healthz")

    def create_case(self, case: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/cases", case)

    def stats(self) -> dict[str, Any]:
        return self._request("GET", "/v1/stats")

    def list_cases(
        self, scope: dict[str, Any] | None = None, query: str | None = None,
        limit: int = 20, cursor: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(max(1, min(50, int(limit))))}
        if scope:
            params["scope"] = json.dumps(scope, separators=(",", ":"), ensure_ascii=True)
        if query:
            params["query"] = str(query)[:500]
        if cursor:
            params["cursor"] = str(cursor)[:2048]
        return self._request("GET", "/v1/cases?" + urlencode(params))

    def search(
        self, scope: dict[str, Any] | None, query: str = "", limit: int = 10, before: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"limit": max(1, min(10, int(limit)))}
        if isinstance(scope, dict) and scope:
            payload["scope"] = scope
        if query:
            payload["query"] = str(query)[:500]
        if before:
            payload["observed_before"] = str(before)[:64]
        return self._request("POST", "/v1/search", payload)

    def list_patterns(
        self, scope: dict[str, Any] | None = None, query: str | None = None,
        limit: int = 10, before: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(max(1, min(50, int(limit))))}
        if scope:
            params["scope"] = json.dumps(scope, separators=(",", ":"), ensure_ascii=True)
        if query:
            params["query"] = str(query)[:500]
        if before:
            params["observed_before"] = str(before)[:64]
        return self._request("GET", "/v1/patterns?" + urlencode(params))

    def get_pattern(self, pattern_id: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/v1/patterns/{quote(str(pattern_id), safe='')}")
        except EstimaClientError as error:
            if "HTTP 404" in str(error):
                return None
            raise

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/v1/cases/{quote(str(case_id), safe='')}")
        except EstimaClientError as error:
            if "HTTP 404" in str(error):
                return None
            raise


def estima_client_from_config(
    config: Mapping[str, Any], operation: str, *, timeout_seconds: float = 2.0,
) -> EstimaClient | None:
    if operation not in {"read", "publish"}:
        raise ValueError("operation must be 'read' or 'publish'")
    enabled_key = "read_enabled" if operation == "read" else "publish_enabled"
    if not bool(config.get(enabled_key)) or not str(config.get("url") or "").strip():
        return None
    return EstimaClient(str(config["url"]), str(config.get("token") or ""), timeout_seconds)


def estima_client_from_env(
    operation: str | None = None, environ: Mapping[str, str] | None = None,
) -> EstimaClient | None:
    """Build an opt-in client; no model API key or model request is used here."""
    config = estima_settings_from_env(environ)
    if operation is None:
        if config["read_enabled"]:
            operation = "read"
        elif config["publish_enabled"]:
            operation = "publish"
        else:
            return None
    return estima_client_from_config(config, operation)


# Compatibility aliases for pre-Estima integrations and local extensions.
AtlasClientError = EstimaClientError
AtlasClient = EstimaClient
atlas_settings_from_env = estima_settings_from_env
atlas_client_from_config = estima_client_from_config
atlas_client_from_env = estima_client_from_env
