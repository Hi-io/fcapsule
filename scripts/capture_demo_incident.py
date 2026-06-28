#!/usr/bin/env python3
"""Run the demo service, trigger a failure, and capture a validated P1 case."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.unstable_service import create_server, utc_now  # noqa: E402
from fcapsule.io.case_loader import load_case  # noqa: E402

ProgressCallback = Callable[[str, str, dict[str, Any]], None]


def _emit(progress: ProgressCallback | None, status: str, message: str, **details: Any) -> None:
    if progress is not None:
        progress(status, message, details)


def request_json(url: str, method: str = "GET") -> tuple[int, dict]:
    request = Request(url, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def capture(output: Path, healthy_requests: int = 60, failing_requests: int = 40, progress: ProgressCallback | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    _emit(progress, "running", "Starting local checkout service")
    with tempfile.TemporaryDirectory(prefix="fcapsule-demo-") as temp_dir:
        raw_log_path = Path(temp_dir) / "service.jsonl"
        server, _state = create_server(raw_log_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        started = datetime.now(timezone.utc)
        snapshots: list[tuple[str, dict]] = []
        alert_started: str | None = None
        try:
            status, health = request_json(f"{base_url}/health")
            if status != 200 or health.get("status") != "ok":
                raise RuntimeError("Demo service did not become healthy")
            _emit(progress, "running", "Service is healthy", service_port=server.server_port, base_url=base_url)
            for index in range(healthy_requests):
                status, _ = request_json(f"{base_url}/checkout")
                if status != 200:
                    raise RuntimeError("Healthy phase returned an unexpected failure")
                _, metrics = request_json(f"{base_url}/metrics")
                snapshots.append((utc_now(), metrics))
                if index == 0 or (index + 1) % 15 == 0 or index + 1 == healthy_requests:
                    _emit(
                        progress,
                        "running",
                        "Sending healthy traffic",
                        healthy_requests=index + 1,
                        target_healthy_requests=healthy_requests,
                        current_error_rate=metrics["request_error_rate"],
                    )

            request_json(f"{base_url}/control/fail", method="POST")
            _emit(progress, "running", "Failure mode enabled: payment dependency is unavailable")
            for index in range(failing_requests):
                status, _ = request_json(f"{base_url}/checkout")
                if status != 503:
                    raise RuntimeError("Failure phase did not return HTTP 503")
                _, metrics = request_json(f"{base_url}/metrics")
                snapshots.append((utc_now(), metrics))
                if metrics["http_requests_total"] >= 10 and metrics["request_error_rate"] > 0.25 and alert_started is None:
                    alert_started = snapshots[-1][0]
                    _emit(
                        progress,
                        "running",
                        "Alert threshold crossed",
                        alert="CheckoutHighErrorRate",
                        request_error_rate=metrics["request_error_rate"],
                        alert_started=alert_started,
                    )
                if index == 0 or (index + 1) % 10 == 0 or index + 1 == failing_requests:
                    _emit(
                        progress,
                        "running",
                        "Sending failing traffic and collecting telemetry",
                        failing_requests=index + 1,
                        target_failing_requests=failing_requests,
                        current_error_rate=metrics["request_error_rate"],
                    )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        if alert_started is None:
            raise RuntimeError("Configured CheckoutHighErrorRate alert did not fire")

        raw_logs = [json.loads(line) for line in raw_log_path.read_text(encoding="utf-8").splitlines() if line]
        _emit(progress, "running", "Raw telemetry captured", raw_logs=len(raw_logs), metric_snapshots=len(snapshots))
        ended = datetime.now(timezone.utc)
        labels = {"service": "checkout-service", "namespace": "checkout", "cluster": "demo-cluster", "pod": "checkout-api-7c9d", "cncc_uuid": "cncc-demo-12345"}
        metric_names = list(snapshots[0][1])
        metric_series = [
            {"metric": name, "labels": labels, "values": [[timestamp, values[name]] for timestamp, values in snapshots]}
            for name in metric_names
        ]
        alert = {
            "alertname": "CheckoutHighErrorRate",
            "status": "firing",
            "severity": "warning",
            "startsAt": alert_started,
            "endsAt": None,
            "labels": labels,
            "annotations": {
                "summary": "Checkout request error rate exceeded 25 percent",
                "description": "The synthetic payment dependency failure caused checkout requests to return HTTP 503.",
            },
        }
        metadata = {
            "case_id": "case_001",
            "case_title": "Checkout dependency failure and elevated error rate",
            **labels,
            "window": {
                "start": (started - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                "end": (ended + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            },
            "timezone": "UTC",
            "telemetry_sources": {"logs": "opensearch_logs.json", "metrics": "prometheus_metrics.json", "alert": "alert.json"},
            "fields": {"log_time_field": "@timestamp", "log_message_field": "message", "log_level_field": "level", "service_label": "cncc_uuid"},
            "privacy": {"anonymized": True, "synthetic": True},
            "notes": ["Generated by scripts/capture_demo_incident.py from a live local service failure."],
        }
        (output / "metadata.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
        (output / "alert.json").write_text(json.dumps(alert, indent=2) + "\n", encoding="utf-8")
        (output / "prometheus_metrics.json").write_text(json.dumps({"window": metadata["window"], "series": metric_series}, indent=2) + "\n", encoding="utf-8")
        (output / "opensearch_logs.json").write_text(json.dumps({"hits": raw_logs}, indent=2) + "\n", encoding="utf-8")
        (output / "expected_notes.md").write_text(
            "# Expected Signals\n\n- CheckoutHighErrorRate must be preserved.\n- Payment dependency unavailable ERROR logs must be preserved.\n- HTTP 503 WARN logs must be preserved.\n- Request error rate and latency anomalies must be selected.\n- Hypotheses must remain tentative and request upstream dependency evidence.\n",
            encoding="utf-8",
        )
        _emit(progress, "running", "Case files written", output=str(output), logs=len(raw_logs), metric_series=len(metric_series))

    bundle = load_case(output)
    result = {
        "case_id": bundle.case_id,
        "service_port": server.server_port,
        "healthy_requests": healthy_requests,
        "failing_requests": failing_requests,
        "captured_logs": len(bundle.logs),
        "metric_series": len(bundle.metrics),
        "alert": bundle.alerts[0]["alertname"],
        "alert_status": bundle.alerts[0]["status"],
        "final_error_rate": snapshots[-1][1]["request_error_rate"],
    }
    _emit(progress, "done", "Capture complete and alert is firing", **result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="cases/case_001", type=Path)
    args = parser.parse_args()
    result = capture(args.output.resolve())
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
