# FCAPSule Data Contracts

## Normalized Incident Case

Every adapter produces a directory with four required files. Kubernetes configuration evidence and regression notes are optional:

```text
metadata.yaml
alert.json
prometheus_metrics.json
opensearch_logs.json
kubernetes_config.json
expected_notes.md
```

### `metadata.yaml`

Required:

- `case_id`
- `case_title`
- `service`
- `namespace`
- `cluster`
- `window.start`
- `window.end`

Supported context:

- `cncc_uuid`, pod, and environment identity;
- `scenario`;
- `fault_injection` or observed configuration changes;
- `topology` relationships;
- `trace_access`;
- telemetry source names;
- source field mapping;
- privacy declarations;
- operator notes.

All timestamps must include a timezone and are compared in UTC.

### `alert.json`

A single object or list:

```json
{
  "alertname": "InventoryPoolSaturation",
  "status": "firing",
  "severity": "critical",
  "startsAt": "2026-08-18T00:00:00Z",
  "endsAt": null,
  "labels": {
    "service": "checkout-platform",
    "component": "inventory-api",
    "namespace": "commerce",
    "cluster": "local-compose"
  },
  "annotations": {
    "summary": "Inventory database pool is saturated",
    "description": "Reservation workers cannot acquire pool slots."
  }
}
```

### `prometheus_metrics.json`

```json
{
  "window": {"start": "...", "end": "..."},
  "series": [
    {
      "metric": "checkout_retry_amplification_ratio",
      "labels": {"service": "checkout-platform", "component": "checkout-api"},
      "values": [["2026-08-18T00:00:00Z", 1.0], ["2026-08-18T00:01:00Z", 2.1]]
    }
  ]
}
```

Each series requires at least two numeric points.

### `opensearch_logs.json`

```json
{
  "hits": [
    {
      "@timestamp": "2026-08-18T00:00:00Z",
      "indexed_at": "2026-08-18T00:00:01Z",
      "level": "ERROR",
      "service": "checkout-platform",
      "component": "inventory-api",
      "message": "Reservation DB pool exhausted active=8 pool_size=8"
    }
  ]
}
```

Field names can be mapped in metadata.

### `kubernetes_config.json`

```json
{
  "items": [
    {
      "kind": "PodSpec",
      "name": "checkout-api-7d9f",
      "namespace": "commerce",
      "images": ["example/checkout:1.4.0"],
      "configmap_refs": ["checkout-runtime"],
      "ready": false
    },
    {
      "kind": "ConfigMap",
      "name": "checkout-runtime",
      "namespace": "commerce",
      "resource_version": "19422",
      "content_hash": "f12c89a6d70e4a13",
      "keys": ["SCHEMA_EPOCH", "WORKER_COUNT"],
      "data": {"SCHEMA_EPOCH": "41", "WORKER_COUNT": "24"}
    }
  ]
}
```

FCAPSule reads only ConfigMaps referenced by the affected pod. Keys that look credential-bearing are redacted, and Kubernetes Secrets are outside the RBAC contract and are never collected.

### `expected_notes.md`

Regression cases may list diagnostic signal groups that should survive selection. These notes are not final root-cause truth. They define review intent and make missing evidence inspectable.

## Trace Access Contract

Trace metadata is stored under `metadata.trace_access`:

```json
{
  "mode": "on_demand",
  "available": true,
  "probe_status": "verified",
  "source_retention_seconds": 900,
  "raw_spans_retained": false,
  "ephemeral_spans_observed": 1266
}
```

Raw spans are never part of the normalized case archive or evidence archive. A live adapter may use them temporarily to derive evidence.

## Capsule Contract

`capsule.json` includes:

- `schema_version`;
- `case`;
- `alerts`;
- `entity_resolution`;
- `timeline`;
- `selected_evidence`;
- `selection_summary`;
- `log_summary` and `log_templates`;
- `metric_anomalies`;
- retained configuration evidence when available;
- `hypotheses`;
- `missing_evidence`;
- `next_steps`;
- `domain_summary`;
- `evaluation`.

Evidence items include:

- stable `evidence_id`;
- source ID and domain;
- title and summary;
- score and score components;
- selection rationale;
- linked entities;
- time range;
- anonymized representative lines when relevant.

## Control-Plane Schema

SQLite tables:

### `applications`

Identity, namespace, cluster, environment, status, source configuration, and timestamps.

### `incidents`

Application reference, scenario, severity, time range, normalized case location, FM/PM/log counts, observed raw bytes, trace-access metadata, and summary.

### `capsules`

Incident/application reference, artifact location, retained bytes, selected evidence count, reduction, signal preservation, grounding, runtime, model winner, and creation time.

### `model_profiles`

Model ID, provider, enabled state, maximum tokens, and update time.

## Artifact Policy

The derived ZIP contains:

- `capsule.json`;
- `capsule.md`;
- `evidence.json`;
- `evaluation.json`;
- `baselines.json`;
- optional comparison outputs.

It excludes normalized raw input files and raw traces.
