"""Small JSON HTTP transport shared by source adapters."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ResponseTooLargeError(RuntimeError):
    """Raised when a response exceeds an explicitly requested byte limit."""


class JsonTransport:
    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 8,
        headers: dict[str, str] | None = None,
        ssl_context: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = dict(headers or {})
        self.ssl_context = ssl_context
        if username:
            credentials = base64.b64encode(f"{username}:{password or ''}".encode()).decode()
            self.headers["Authorization"] = f"Basic {credentials}"

    def request(
        self,
        path: str,
        method: str = "GET",
        body: Any = None,
        max_response_bytes: int | None = None,
    ) -> Any:
        if max_response_bytes is not None and max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", **self.headers}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout, context=self.ssl_context) as response:
                raw = response.read() if max_response_bytes is None else response.read(max_response_bytes + 1)
                if max_response_bytes is not None and len(raw) > max_response_bytes:
                    raise ResponseTooLargeError(
                        f"Response from {self.base_url}{path} exceeded the {max_response_bytes}-byte limit"
                    )
                return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read(401).decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"HTTP {exc.code} from {self.base_url}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Cannot reach {self.base_url}: {exc}") from exc
