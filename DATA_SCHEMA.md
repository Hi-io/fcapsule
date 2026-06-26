# FCAPSule AI P1 Data Schema

## Case Folder

Every case is self-contained:

```text
cases/<case_id>/
  metadata.yaml
  alert.json
  prometheus_metrics.json
  opensearch_logs.json
  expected_notes.md       # optional but recommended
```

P1 requires the first four files. Validation errors identify the file and field that failed.

## `metadata.yaml`

Required fields:

| Field | Type | Meaning |
|---|---|---|
| `case_id` | string | Stable case identifier |
| `case_title` | string | Human-readable title |
| `service` | string | Primary affected service |
| `cluster` | string | Cluster identity |
| `namespace` | string | Namespace identity |
| `window.start` | ISO-8601 string | Inclusive telemetry start |
| `window.end` | ISO-8601 string | Telemetry end; must be after start |

Recommended fields: `pod`, `cncc_uuid`, `timezone`, `telemetry_sources`, `fields`, `privacy`, and `notes`.

All timestamps must include a timezone. P1 normalizes comparisons to UTC.

## `alert.json`

The file may contain one alert object or a list of alert objects.

Required alert fields:

| Field | Type |
|---|---|
| `alertname` | string |
| `status` | string |
| `severity` | string |
| `startsAt` | ISO-8601 string |
| `labels` | object |

Optional fields: `endsAt` and `annotations`. Entity labels should use `service`, `namespace`, `cluster`, `pod`, and `cncc_uuid` when available.

## `prometheus_metrics.json`

```json
{
  "window": {"start": "...", "end": "..."},
  "series": [
    {
      "metric": "request_error_rate",
      "labels": {"pod": "checkout-api-7c9d"},
      "values": [["2026-06-21T10:00:00Z", 0.05]]
    }
  ]
}
```

Each series needs a metric name, labels object, and at least two numeric timestamp/value points. Metrics ending in `_total` are treated as counters and analyzed using non-negative per-sample deltas. Other series are treated as gauges.

## `opensearch_logs.json`

```json
{
  "hits": [
    {
      "@timestamp": "2026-06-21T10:01:22Z",
      "level": "ERROR",
      "message": "Failed to connect to 10.0.0.3 after 3 retries",
      "service": "checkout-service",
      "namespace": "checkout",
      "pod": "checkout-api-7c9d",
      "cluster": "demo-cluster",
      "cncc_uuid": "cncc-demo-12345"
    }
  ]
}
```

Field names can be overridden in `metadata.yaml.fields`. Timestamp and message are required.

## `expected_notes.md`

This optional manual reference lists signals a reviewer expects the capsule to preserve. It is not treated as root-cause truth. In P1 it supports regression review and documents case intent.

## Output Contracts

### `capsule.json`

Contains:

- `schema_version`;
- case metadata and alerts;
- `domain_summary` describing the P1 operational telemetry domains;
- entity resolution and warnings;
- timeline;
- selected evidence and selection summary;
- log and metric analyses;
- verified hypotheses;
- missing evidence and next checks;
- objective evaluation.

### `evidence.json`

Contains every candidate evidence item, including candidates not selected. Each item records:

- stable `evidence_id` and source ID;
- explicit telemetry `domain` and evidence `type`;
- title and summary;
- total score;
- every intermediate score component;
- selection rationale;
- linked entities and time range;
- anonymized representative lines when applicable.

### `evaluation.json`

Contains objective metrics and inline definitions. Ratios use values from `0.0` to `1.0`.

### `baselines.json`

Contains raw, keyword-filter, time-window sample, and LLM comparison protocol data. The deterministic P1 records LLM comparison outputs only when an approved provider is configured and `compare-llms` is run.

### `llm_comparison.json`

Created by `compare-llms`. It contains the exact prompt, compared model names, parsed responses when available, raw assistant content, token usage, latency, citation validity, domain coverage, expected signal coverage, actionability score, winner, and score delta. API keys are never written to this file.

### `dashboard.html`

Static local review UI generated from `capsule.json`, `evaluation.json`, and optional `llm_comparison.json`. It is a derived artifact and can be regenerated.

### Archive

`fcapsule_<case_id>.zip` contains derived output files. It excludes raw telemetry and credentials by design.
