"""Real local multi-service incident used by the FCAPSule simulation lab."""

from __future__ import annotations

import json
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import yaml

from fcapsule.io.case_loader import load_case


ProgressCallback = Callable[[str, str, dict[str, Any]], None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _emit(progress: ProgressCallback | None, status: str, message: str, **details: Any) -> None:
    if progress:
        progress(status, message, details)


def _percentile(values: list[float], percentile: float = 0.95) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * percentile)))
    return ordered[index]


class JsonLogSink:
    def __init__(self, path: Path, app_name: str) -> None:
        self.path = path
        self.app_name = app_name
        self.count = 0
        self.lock = threading.Lock()

    def write(self, component: str, level: str, message: str, **fields: Any) -> None:
        event = {
            "@timestamp": utc_now(),
            "indexed_at": utc_now(),
            "level": level,
            "message": message,
            "service": self.app_name,
            "component": component,
            "namespace": "commerce",
            "cluster": "local-lab",
            "pod": f"{component}-7d8c9",
            "cncc_uuid": "cncc-lab-commerce-001",
            **fields,
        }
        with self.lock:
            self.count += 1
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=True) + "\n")


class InventoryState:
    def __init__(self, sink: JsonLogSink, pool_size: int = 8) -> None:
        self.sink = sink
        self.pool_size = pool_size
        self.pool = threading.BoundedSemaphore(pool_size)
        self.degraded = False
        self.calls = 0
        self.errors = 0
        self.slow_queries = 0
        self.pool_exhausted = 0
        self.active_connections = 0
        self.peak_connections = 0
        self.latencies_ms: list[float] = []
        self.lock = threading.Lock()

    def enable_partition_contention(self) -> None:
        with self.lock:
            self.degraded = True
        self.sink.write(
            "inventory-api",
            "WARN",
            "Runtime config reloaded reservation_partition_lock_timeout_ms=85 pool_size=8",
            config_version="inventory-2026.08.18-rc3",
        )

    def reserve(self, order_number: int) -> tuple[int, dict[str, Any]]:
        started = time.perf_counter()
        acquired = self.pool.acquire(timeout=0.006)
        with self.lock:
            self.calls += 1
        if not acquired:
            with self.lock:
                self.errors += 1
                self.pool_exhausted += 1
            self.sink.write(
                "inventory-api",
                "ERROR",
                "Reservation DB pool exhausted wait_ms=6 active=8 pool_size=8",
                order_number=order_number,
            )
            return 503, {"status": "pool_exhausted"}

        with self.lock:
            self.active_connections += 1
            self.peak_connections = max(self.peak_connections, self.active_connections)
            degraded = self.degraded
        try:
            affected_partition = degraded and order_number % 5 != 0
            if affected_partition:
                time.sleep(0.070)
                with self.lock:
                    self.slow_queries += 1
                self.sink.write(
                    "inventory-api",
                    "WARN",
                    "Reservation query exceeded client deadline partition=eu-west-3 lock_wait_ms=70",
                    order_number=order_number,
                )
            else:
                time.sleep(0.0015)
                self.sink.write(
                    "inventory-api",
                    "INFO",
                    "Inventory reservation committed partition=eu-west-1 rows=1",
                    order_number=order_number,
                )
            return 200, {"status": "reserved", "partition": "eu-west-3" if affected_partition else "eu-west-1"}
        finally:
            latency = (time.perf_counter() - started) * 1000
            with self.lock:
                self.active_connections -= 1
                self.latencies_ms.append(latency)
            self.pool.release()

    def metrics(self) -> dict[str, float]:
        with self.lock:
            return {
                "inventory_reservation_calls_total": float(self.calls),
                "inventory_reservation_errors_total": float(self.errors),
                "inventory_slow_queries_total": float(self.slow_queries),
                "inventory_db_pool_exhausted_total": float(self.pool_exhausted),
                "inventory_db_pool_active_connections": float(self.active_connections),
                "inventory_db_pool_peak_utilization_ratio": self.peak_connections / self.pool_size,
                "inventory_reservation_latency_p95_ms": _percentile(self.latencies_ms),
            }


class CheckoutState:
    def __init__(self, sink: JsonLogSink, inventory_url: str) -> None:
        self.sink = sink
        self.inventory_url = inventory_url
        self.requests = 0
        self.errors = 0
        self.successes = 0
        self.retry_attempts = 0
        self.inventory_attempts = 0
        self.active_requests = 0
        self.peak_active_requests = 0
        self.latencies_ms: list[float] = []
        self.ephemeral_span_count = 0
        self.lock = threading.Lock()

    def checkout(self, order_number: int) -> tuple[int, dict[str, Any]]:
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        with self.lock:
            self.active_requests += 1
            self.peak_active_requests = max(self.peak_active_requests, self.active_requests)
            self.ephemeral_span_count += 1
        self.sink.write("checkout-api", "INFO", "Checkout request accepted", request_id=request_id, order_number=order_number)

        success = False
        for attempt in range(1, 4):
            with self.lock:
                self.inventory_attempts += 1
                if attempt > 1:
                    self.retry_attempts += 1
                self.ephemeral_span_count += 1
            try:
                request = Request(f"{self.inventory_url}/reserve?order={order_number}")
                with urlopen(request, timeout=0.028) as response:
                    if response.status == 200:
                        success = True
                        break
            except (HTTPError, URLError, TimeoutError):
                pass
            self.sink.write(
                "checkout-api",
                "WARN",
                f"Inventory reservation attempt failed attempt={attempt} max_attempts=3 breaker_state=closed",
                request_id=request_id,
                order_number=order_number,
            )

        latency = (time.perf_counter() - started) * 1000
        with self.lock:
            self.requests += 1
            self.successes += int(success)
            self.errors += int(not success)
            self.active_requests -= 1
            self.latencies_ms.append(latency)
        if success:
            self.sink.write(
                "checkout-api",
                "INFO",
                "Checkout completed inventory_reserved=true payment_authorized=true",
                request_id=request_id,
                order_number=order_number,
                duration_ms=round(latency, 2),
            )
            return 200, {"status": "completed", "request_id": request_id}

        self.sink.write(
            "checkout-api",
            "ERROR",
            "Checkout aborted after inventory retry budget exhausted breaker_state=closed",
            request_id=request_id,
            order_number=order_number,
            duration_ms=round(latency, 2),
        )
        return 503, {"status": "inventory_unavailable", "request_id": request_id}

    def metrics(self) -> dict[str, float]:
        with self.lock:
            requests = max(1, self.requests)
            return {
                "checkout_http_requests_total": float(self.requests),
                "checkout_http_errors_total": float(self.errors),
                "checkout_http_success_total": float(self.successes),
                "checkout_request_error_rate": self.errors / requests,
                "checkout_request_latency_p95_ms": _percentile(self.latencies_ms),
                "checkout_inventory_attempts_total": float(self.inventory_attempts),
                "checkout_inventory_retries_total": float(self.retry_attempts),
                "checkout_retry_amplification_ratio": self.inventory_attempts / requests,
                "checkout_active_requests": float(self.active_requests),
                "checkout_peak_active_requests": float(self.peak_active_requests),
                "checkout_circuit_breaker_open": 0.0,
            }


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args: Any) -> None:
        return

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return


def inventory_handler(state: InventoryState) -> type[QuietHandler]:
    class Handler(QuietHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self.send_json(200, {"status": "ok", "degraded": state.degraded})
                return
            if parsed.path == "/trace-probe":
                self.send_json(200, {"available": True, "retention_seconds": 900, "raw_spans_returned": 0})
                return
            if parsed.path != "/reserve":
                self.send_json(404, {"error": "not_found"})
                return
            query = parse_qs(parsed.query)
            order = int(query.get("order", ["0"])[0])
            status, payload = state.reserve(order)
            self.send_json(status, payload)

    return Handler


def checkout_handler(state: CheckoutState) -> type[QuietHandler]:
    class Handler(QuietHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self.send_json(200, {"status": "ok"})
                return
            if parsed.path != "/checkout":
                self.send_json(404, {"error": "not_found"})
                return
            query = parse_qs(parsed.query)
            order = int(query.get("order", ["0"])[0])
            status, payload = state.checkout(order)
            self.send_json(status, payload)

    return Handler


@dataclass(frozen=True)
class SimulationConfig:
    app_id: str = "checkout-platform"
    app_name: str = "Checkout Platform"
    baseline_requests: int = 180
    incident_requests: int = 240
    concurrency: int = 24
    scenario: str = "inventory-lock-contention"

    def validate(self) -> None:
        if not self.app_id or any(character.isspace() for character in self.app_id):
            raise ValueError("app_id must be a non-empty identifier without spaces")
        if self.baseline_requests < 20 or self.incident_requests < 20:
            raise ValueError("baseline_requests and incident_requests must be at least 20")
        if self.baseline_requests > 5000 or self.incident_requests > 5000:
            raise ValueError("request counts cannot exceed 5000")
        if self.concurrency < 1 or self.concurrency > 64:
            raise ValueError("concurrency must be between 1 and 64")
        if self.scenario != "inventory-lock-contention":
            raise ValueError(f"Unknown incident scenario: {self.scenario}")


def _request(url: str) -> int:
    try:
        with urlopen(url, timeout=3) as response:
            response.read()
            return response.status
    except HTTPError as exc:
        exc.read()
        return exc.code


def _traffic(base_url: str, start: int, count: int, concurrency: int) -> tuple[int, int]:
    successes = 0
    failures = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(_request, f"{base_url}/checkout?order={order}") for order in range(start, start + count)]
        for future in as_completed(futures):
            if future.result() == 200:
                successes += 1
            else:
                failures += 1
    return successes, failures


def _snapshot(checkout: CheckoutState, inventory: InventoryState, sink: JsonLogSink) -> dict[str, float]:
    values = {**checkout.metrics(), **inventory.metrics()}
    values["application_log_events_total"] = float(sink.count)
    values["log_indexing_delay_p95_seconds"] = 1.8 if inventory.degraded else 0.12
    values["prometheus_scrape_health"] = 1.0
    return values


def _alert(name: str, severity: str, starts_at: str, labels: dict[str, str], summary: str, description: str) -> dict[str, Any]:
    return {
        "alertname": name,
        "status": "firing",
        "severity": severity,
        "startsAt": starts_at,
        "endsAt": None,
        "labels": labels,
        "annotations": {"summary": summary, "description": description},
    }


def run_simulation(
    output: str | Path,
    config: SimulationConfig | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the local incident and write a normalized FCAPSule case bundle."""

    config = config or SimulationConfig()
    config.validate()
    case_dir = Path(output).resolve()
    case_dir.mkdir(parents=True, exist_ok=True)
    incident_id = f"incident-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    raw_log_path = case_dir / ".runtime-logs.jsonl"
    raw_log_path.unlink(missing_ok=True)
    sink = JsonLogSink(raw_log_path, config.app_id)
    inventory = InventoryState(sink)
    inventory_server = ThreadingHTTPServer(("127.0.0.1", 0), inventory_handler(inventory))
    inventory_url = f"http://127.0.0.1:{inventory_server.server_port}"
    checkout = CheckoutState(sink, inventory_url)
    checkout_server = ThreadingHTTPServer(("127.0.0.1", 0), checkout_handler(checkout))
    checkout_url = f"http://127.0.0.1:{checkout_server.server_port}"
    threads = [
        threading.Thread(target=inventory_server.serve_forever, daemon=True),
        threading.Thread(target=checkout_server.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()

    started = datetime.now(timezone.utc)
    snapshots: list[tuple[str, dict[str, float]]] = []
    alert_times: dict[str, str] = {}
    try:
        _emit(progress, "running", "Starting checkout and inventory services", services=2, incident_id=incident_id)
        if _request(f"{checkout_url}/health") != 200 or _request(f"{inventory_url}/health") != 200:
            raise RuntimeError("The simulation services did not become healthy")
        _emit(progress, "running", "Running baseline traffic", requests=config.baseline_requests, concurrency=min(8, config.concurrency))
        batch_size = max(10, min(30, config.baseline_requests // 6))
        completed = 0
        while completed < config.baseline_requests:
            count = min(batch_size, config.baseline_requests - completed)
            _traffic(checkout_url, completed, count, min(8, config.concurrency))
            completed += count
            snapshots.append((utc_now(), _snapshot(checkout, inventory, sink)))
            _emit(progress, "running", "Baseline traffic captured", completed=completed, total=config.baseline_requests)

        inventory.enable_partition_contention()
        injection_time = utc_now()
        _emit(
            progress,
            "running",
            "Injected inventory partition lock contention",
            component="inventory-api",
            configuration="inventory-2026.08.18-rc3",
        )
        batch_size = max(12, min(40, config.incident_requests // 8))
        completed = 0
        while completed < config.incident_requests:
            count = min(batch_size, config.incident_requests - completed)
            successes, failures = _traffic(
                checkout_url,
                config.baseline_requests + completed,
                count,
                config.concurrency,
            )
            completed += count
            values = _snapshot(checkout, inventory, sink)
            timestamp = utc_now()
            snapshots.append((timestamp, values))
            if values["checkout_retry_amplification_ratio"] > 1.25:
                alert_times.setdefault("CheckoutRetryAmplification", timestamp)
            if values["inventory_db_pool_exhausted_total"] > 0:
                alert_times.setdefault("InventoryPoolSaturation", timestamp)
            if values["checkout_request_error_rate"] > 0.12:
                alert_times.setdefault("CheckoutErrorBudgetBurn", timestamp)
            _emit(
                progress,
                "running",
                "Incident traffic captured",
                completed=completed,
                total=config.incident_requests,
                batch_successes=successes,
                batch_failures=failures,
                retry_amplification=round(values["checkout_retry_amplification_ratio"], 2),
                error_rate=round(values["checkout_request_error_rate"], 3),
            )

        status, trace_probe = 200, {}
        try:
            with urlopen(f"{inventory_url}/trace-probe", timeout=2) as response:
                status = response.status
                trace_probe = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError):
            status = 503
        _emit(progress, "running", "Verified on-demand trace access", available=status == 200, raw_spans_retained=False)
    finally:
        checkout_server.shutdown()
        inventory_server.shutdown()
        checkout_server.server_close()
        inventory_server.server_close()
        for thread in threads:
            thread.join(timeout=5)

    if len(alert_times) < 3:
        raise RuntimeError(f"The incident did not trigger the expected alert sequence: {sorted(alert_times)}")

    ended = datetime.now(timezone.utc)
    labels = {
        "service": config.app_id,
        "namespace": "commerce",
        "cluster": "local-lab",
        "cncc_uuid": "cncc-lab-commerce-001",
    }
    alerts = [
        _alert(
            "CheckoutRetryAmplification",
            "warning",
            alert_times["CheckoutRetryAmplification"],
            {**labels, "component": "checkout-api"},
            "Inventory retries exceed the request baseline",
            "Checkout inventory calls are being amplified while the circuit breaker remains closed.",
        ),
        _alert(
            "InventoryPoolSaturation",
            "critical",
            alert_times["InventoryPoolSaturation"],
            {**labels, "component": "inventory-api"},
            "Inventory database pool is saturated",
            "Reservation workers cannot acquire database pool slots during partition lock contention.",
        ),
        _alert(
            "CheckoutErrorBudgetBurn",
            "critical",
            alert_times["CheckoutErrorBudgetBurn"],
            {**labels, "component": "checkout-api"},
            "Checkout error budget burn is elevated",
            "Checkout failures exceed the configured burn-rate threshold after inventory retries are exhausted.",
        ),
    ]
    logs = [json.loads(line) for line in raw_log_path.read_text(encoding="utf-8").splitlines() if line]
    raw_log_path.unlink(missing_ok=True)
    metric_names = list(snapshots[0][1])
    metric_series = []
    for name in metric_names:
        component = "inventory-api" if name.startswith("inventory_") else "checkout-api"
        if name.startswith(("application_log", "log_indexing", "prometheus_")):
            component = "telemetry-pipeline"
        metric_series.append(
            {
                "metric": name,
                "labels": {**labels, "component": component},
                "values": [[timestamp, values[name]] for timestamp, values in snapshots],
            }
        )

    trace_access = {
        "mode": "on_demand",
        "available": bool(trace_probe.get("available")),
        "probe_status": "verified" if trace_probe.get("available") else "unavailable",
        "source_retention_seconds": int(trace_probe.get("retention_seconds", 0)),
        "raw_spans_retained": False,
        "ephemeral_spans_observed": checkout.ephemeral_span_count,
    }
    metadata = {
        "case_id": incident_id,
        "case_title": "Inventory lock contention amplified by checkout retries",
        "service": config.app_id,
        "cluster": "local-lab",
        "namespace": "commerce",
        "cncc_uuid": "cncc-lab-commerce-001",
        "window": {
            "start": (started - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "end": (ended + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        },
        "timezone": "UTC",
        "scenario": config.scenario,
        "workload": {
            "baseline_requests": config.baseline_requests,
            "incident_requests": config.incident_requests,
            "concurrency": config.concurrency,
        },
        "incident_metrics": {
            "retry_amplification": round(final_metrics["checkout_retry_amplification_ratio"], 3),
            "error_rate": round(final_metrics["checkout_request_error_rate"], 4),
            "pool_peak_utilization": round(final_metrics["inventory_db_pool_peak_utilization_ratio"], 3),
        },
        "fault_injection": {"timestamp": injection_time, "configuration": "inventory-2026.08.18-rc3"},
        "topology": [
            {"from": "edge-gateway", "to": "checkout-api", "protocol": "HTTP"},
            {"from": "checkout-api", "to": "inventory-api", "protocol": "HTTP"},
            {"from": "inventory-api", "to": "reservation-db", "protocol": "SQL pool"},
        ],
        "trace_access": trace_access,
        "telemetry_sources": {
            "logs": "opensearch_logs.json",
            "metrics": "prometheus_metrics.json",
            "faults": "alert.json",
            "traces": "on-demand source probe only",
        },
        "fields": {
            "log_time_field": "@timestamp",
            "log_message_field": "message",
            "log_level_field": "level",
            "service_label": "cncc_uuid",
        },
        "privacy": {"anonymized": True, "synthetic": True},
        "notes": [
            "Generated from concurrent HTTP traffic against live local checkout and inventory services.",
            "Raw spans were available in the ephemeral source buffer and were not retained by FCAPSule.",
        ],
    }
    (case_dir / "metadata.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    (case_dir / "alert.json").write_text(json.dumps(alerts, indent=2) + "\n", encoding="utf-8")
    (case_dir / "prometheus_metrics.json").write_text(
        json.dumps({"window": metadata["window"], "series": metric_series}, indent=2) + "\n",
        encoding="utf-8",
    )
    (case_dir / "opensearch_logs.json").write_text(json.dumps({"hits": logs}, indent=2) + "\n", encoding="utf-8")
    (case_dir / "expected_notes.md").write_text(
        """# Expected Signals

- Preserve the CheckoutRetryAmplification, InventoryPoolSaturation, and CheckoutErrorBudgetBurn fault sequence.
- Preserve checkout retry exhaustion and inventory DB pool exhaustion log patterns.
- Preserve retry amplification, pool utilization, pool exhaustion, error-rate, and latency metric changes.
- Recognize that the circuit breaker remained closed while retries amplified dependency pressure.
- Treat inventory lock contention plus retry amplification as an investigation path, not a proven final root cause.
- Record that traces were queryable on demand but raw spans were not retained.
""",
        encoding="utf-8",
    )

    bundle = load_case(case_dir)
    raw_files = [case_dir / name for name in ("metadata.yaml", "alert.json", "prometheus_metrics.json", "opensearch_logs.json")]
    final_metrics = snapshots[-1][1]
    result = {
        "incident_id": incident_id,
        "case_id": bundle.case_id,
        "app_id": config.app_id,
        "app_name": config.app_name,
        "scenario": config.scenario,
        "status": "firing",
        "severity": "critical",
        "started_at": alerts[0]["startsAt"],
        "ended_at": None,
        "case_dir": str(case_dir),
        "baseline_requests": config.baseline_requests,
        "incident_requests": config.incident_requests,
        "concurrency": config.concurrency,
        "successful_requests": checkout.successes,
        "failed_requests": checkout.errors,
        "alert_count": len(bundle.alerts),
        "alerts": [item["alertname"] for item in bundle.alerts],
        "log_count": len(bundle.logs),
        "metric_series_count": len(bundle.metrics),
        "raw_bytes": sum(path.stat().st_size for path in raw_files),
        "trace_access": trace_access,
        "retry_amplification": round(final_metrics["checkout_retry_amplification_ratio"], 3),
        "error_rate": round(final_metrics["checkout_request_error_rate"], 4),
        "pool_peak_utilization": round(final_metrics["inventory_db_pool_peak_utilization_ratio"], 3),
        "summary": "Inventory partition lock contention was followed by retry amplification, pool saturation, and checkout errors.",
    }
    progress_result = {key: value for key, value in result.items() if key != "status"}
    _emit(progress, "done", "Incident case captured", **progress_result)
    return result
