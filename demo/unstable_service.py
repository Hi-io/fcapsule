"""Small HTTP service with a deliberate dependency failure mode."""

from __future__ import annotations

import json
import statistics
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ServiceState:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.failure_mode = False
        self.requests = 0
        self.errors = 0
        self.dependency_failures = 0
        self.log_events = 0
        self.latencies_ms: list[float] = []
        self.lock = threading.Lock()

    def set_failure_mode(self, enabled: bool) -> None:
        with self.lock:
            self.failure_mode = enabled
        self.log("WARN", "Failure mode changed enabled=%s" % str(enabled).lower())

    def log(self, level: str, message: str) -> None:
        event = {
            "@timestamp": utc_now(),
            "level": level,
            "message": message,
            "service": "checkout-service",
            "namespace": "checkout",
            "pod": "checkout-api-7c9d",
            "cluster": "demo-cluster",
            "cncc_uuid": "cncc-demo-12345",
        }
        with self.lock:
            self.log_events += 1
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event) + "\n")

    def record_request(self, latency_ms: float, failed: bool) -> None:
        with self.lock:
            self.requests += 1
            self.errors += int(failed)
            self.dependency_failures += int(failed)
            self.latencies_ms.append(latency_ms)

    def metrics(self) -> dict[str, float]:
        with self.lock:
            request_count = self.requests
            errors = self.errors
            sorted_latencies = sorted(self.latencies_ms)
            p95_index = max(0, min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95)))
            p95 = sorted_latencies[p95_index] if sorted_latencies else 0.0
            average = statistics.fmean(sorted_latencies) if sorted_latencies else 0.0
            return {
                "http_requests_total": float(request_count),
                "http_request_errors_total": float(errors),
                "request_error_rate": errors / request_count if request_count else 0.0,
                "http_request_latency_p95_ms": p95,
                "http_request_latency_average_ms": average,
                "payment_dependency_failures_total": float(self.dependency_failures),
                "application_log_events_total": float(self.log_events),
            }


def build_handler(state: ServiceState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "FCAPSuleDemo/1.0"

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._json(200, {"status": "ok", "failure_mode": state.failure_mode})
                return
            if self.path == "/metrics":
                self._json(200, state.metrics())
                return
            if self.path != "/checkout":
                self._json(404, {"error": "not found"})
                return

            started = time.perf_counter()
            request_id = uuid.uuid4().hex
            if state.failure_mode:
                time.sleep(0.035)
                latency = (time.perf_counter() - started) * 1000
                state.record_request(latency, True)
                state.log("ERROR", f"Payment dependency 10.0.0.3 unavailable after 3 retries request_id={request_id}")
                state.log("WARN", f"Checkout request failed status=503 duration_ms={latency:.2f} request_id={request_id}")
                self._json(503, {"status": "failed", "request_id": request_id})
                return

            time.sleep(0.002)
            latency = (time.perf_counter() - started) * 1000
            state.record_request(latency, False)
            state.log("INFO", f"Checkout request completed status=200 duration_ms={latency:.2f} request_id={request_id}")
            self._json(200, {"status": "accepted", "request_id": request_id})

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/control/fail":
                state.set_failure_mode(True)
                self._json(200, {"failure_mode": True})
            elif self.path == "/control/recover":
                state.set_failure_mode(False)
                self._json(200, {"failure_mode": False})
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def create_server(log_path: str | Path, port: int = 0) -> tuple[ThreadingHTTPServer, ServiceState]:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    state = ServiceState(path)
    server = ThreadingHTTPServer(("127.0.0.1", port), build_handler(state))
    return server, state
