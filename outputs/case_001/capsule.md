# FCAPSule AI Evidence Capsule

## 1. Case Summary

**Checkout dependency failure and elevated error rate** (`case_001`) affects `checkout-service` in `checkout` / `demo-cluster`.

This capsule records observed telemetry and ranked investigation paths. It does not assert a final root cause.

## 2. Alert Context

- **CheckoutHighErrorRate** is `firing` with `warning` severity.
- Started at `2026-06-28T13:43:39.605022Z`.
- The synthetic payment dependency failure caused checkout requests to return HTTP 503.

## 3. Telemetry Window

`2026-06-28T13:43:37.545405Z` to `2026-06-28T13:43:41.819841Z` (UTC).

## 4. Multidomain Telemetry Map

In FCAPSule P1, a domain is a telemetry signal family with its own data shape and analysis method. It is similar to how text, audio, and images are different AI modalities, but here the domains are operational telemetry: fault events, log text, time-series metrics, topology metadata, and LLM reasoning.

- **Fault events** (`fault_events`): Discrete alert and incident event stream; selected evidence `1`; P1 role: Defines the incident trigger and the investigation window.
- **Log text** (`log_text`): Semi-structured textual telemetry; selected evidence `4`; P1 role: Explains repeated application behavior without retaining every raw line.
- **Time-series metrics** (`time_series_metrics`): Numeric measurements over time; selected evidence `7`; P1 role: Shows whether numeric behavior changed during the alert window.
- **Topology and metadata** (`topology_metadata`): Entity identity and relationship context; selected evidence `0`; P1 role: Connects evidence from different telemetry domains to the same system entity.
- **LLM reasoning** (`llm_reasoning`): Generated evidence-grounded interpretation; selected evidence `0`; P1 role: Turns selected evidence into structured investigation notes while preserving citations.

## 5. Incident Timeline

- `2026-06-28T13:43:39.605022Z` **alert**: CheckoutHighErrorRate - The synthetic payment dependency failure caused checkout requests to return HTTP 503.

## 6. Selected Evidence

1. **Payment dependency <IP> unavailable after <NUM> retries request_id=<ID>** (`ev_log_template_003`, `log_text`, score `0.934`): 40 matching logs (28.4% of the case).
2. **http_request_errors_total** (`ev_metric_002`, `time_series_metrics`, score `0.925`): http_request_errors_total per-sample increase increased 1000.0% near the alert compared with the baseline median.
3. **request_error_rate** (`ev_metric_003`, `time_series_metrics`, score `0.925`): request_error_rate value increased 1000.0% near the alert compared with the baseline median.
4. **http_request_latency_p95_ms** (`ev_metric_004`, `time_series_metrics`, score `0.925`): http_request_latency_p95_ms value increased 1473.2% near the alert compared with the baseline median.
5. **http_request_latency_average_ms** (`ev_metric_005`, `time_series_metrics`, score `0.925`): http_request_latency_average_ms value increased 611.3% near the alert compared with the baseline median.
6. **application_log_events_total** (`ev_metric_007`, `time_series_metrics`, score `0.925`): application_log_events_total per-sample increase increased 100.0% near the alert compared with the baseline median.
7. **CheckoutHighErrorRate** (`ev_alert_001`, `fault_events`, score `0.887`): The synthetic payment dependency failure caused checkout requests to return HTTP 503.
8. **Checkout request failed status=<NUM> duration_ms=<NUM> request_id=<ID>** (`ev_log_template_002`, `log_text`, score `0.859`): 40 matching logs (28.4% of the case).
9. **payment_dependency_failures_total** (`ev_metric_006`, `time_series_metrics`, score `0.835`): payment_dependency_failures_total per-sample increase increased 1000.0% near the alert compared with the baseline median.
10. **Checkout request completed status=<NUM> duration_ms=<NUM> request_id=<ID>** (`ev_log_template_001`, `log_text`, score `0.625`): 60 matching logs (42.6% of the case).
11. **Failure mode changed enabled=true** (`ev_log_template_004`, `log_text`, score `0.622`): 1 matching logs (0.7% of the case).
12. **http_requests_total** (`ev_metric_001`, `time_series_metrics`, score `0.525`): http_requests_total per-sample increase increased 0.0% near the alert compared with the baseline median.

## 7. Log Compression Summary

- Raw lines: `141`
- Grouped templates: `4`
- Selected representative lines: `7`

- `log_template_001`: Checkout request completed status=<NUM> duration_ms=<NUM> request_id=<ID> - 60 lines, levels {'INFO': 60}
- `log_template_002`: Checkout request failed status=<NUM> duration_ms=<NUM> request_id=<ID> - 40 lines, levels {'WARN': 40}
- `log_template_003`: Payment dependency <IP> unavailable after <NUM> retries request_id=<ID> - 40 lines, levels {'ERROR': 40}
- `log_template_004`: Failure mode changed enabled=true - 1 lines, levels {'WARN': 1}

## 8. Metric Anomalies

- `metric_007` **application_log_events_total** (score `1.000`): application_log_events_total per-sample increase increased 100.0% near the alert compared with the baseline median.
- `metric_002` **http_request_errors_total** (score `1.000`): http_request_errors_total per-sample increase increased 1000.0% near the alert compared with the baseline median.
- `metric_005` **http_request_latency_average_ms** (score `1.000`): http_request_latency_average_ms value increased 611.3% near the alert compared with the baseline median.
- `metric_004` **http_request_latency_p95_ms** (score `1.000`): http_request_latency_p95_ms value increased 1473.2% near the alert compared with the baseline median.
- `metric_006` **payment_dependency_failures_total** (score `1.000`): payment_dependency_failures_total per-sample increase increased 1000.0% near the alert compared with the baseline median.
- `metric_003` **request_error_rate** (score `1.000`): request_error_rate value increased 1000.0% near the alert compared with the baseline median.
- `metric_001` **http_requests_total** (score `0.000`): http_requests_total per-sample increase increased 0.0% near the alert compared with the baseline median.

## 9. Ranked Investigation Hypotheses

### hyp_001: A dependency or request-processing failure may be driving the observed service errors.

- Verdict: `plausible`; adjusted confidence: `0.807`.
- Supporting evidence: `ev_log_template_003`, `ev_metric_002`, `ev_alert_001`.
- Verification: All cited evidence IDs exist in the selected evidence set. Missing telemetry prevents a stronger causal conclusion.

### hyp_002: Elevated request latency may be part of the same degradation window as the alert.

- Verdict: `plausible`; adjusted confidence: `0.750`.
- Supporting evidence: `ev_metric_004`, `ev_log_template_003`.
- Verification: All cited evidence IDs exist in the selected evidence set. Missing telemetry prevents a stronger causal conclusion.

### hyp_003: Higher application log production may add ingestion pressure, but an effect on telemetry completeness is not established.

- Verdict: `plausible`; adjusted confidence: `0.689`.
- Supporting evidence: `ev_metric_007`, `ev_alert_001`.
- Verification: All cited evidence IDs exist in the selected evidence set. Missing telemetry prevents a stronger causal conclusion.

## 10. Missing Evidence

- Kafka consumer lag and OpenSearch indexing queue metrics
- Per-endpoint latency and saturation metrics
- Upstream dependency health and distributed traces

## 11. Suggested Next Steps

- Inspect dependency health during the alert window.
- Trace failed requests across the affected pod and upstream service.
- Break latency down by endpoint and dependency.
- Check CPU, memory, and worker saturation around the peak.
- Check log pipeline lag and rejected events.
- Confirm whether late-arriving logs exist after the captured window.

## 12. Retention Note

If raw telemetry expires, this capsule preserves the alert context, affected entities, event timing, anonymized representative log patterns, metric changes, evidence scores, grounded hypotheses, missing-data warnings, and next checks. Raw logs are intentionally not copied into the capsule archive.

## 13. Evaluation Summary

- Log compression: `95.0%`
- Template reduction: `97.2%`
- Signal preservation: `100.0%`
- Grounded hypothesis citations: `100.0%`
- Retention survivability: `100.0%`
