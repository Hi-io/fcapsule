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

FCAPSule reads ConfigMaps referenced by observed pods. PodSpec records also retain resource requests/limits, pod UID, container restart counts and current/last termination reason, exit code and timestamps when available. Arbitrary termination messages are excluded. Keys that look credential-bearing are redacted, and Kubernetes Secrets are outside the RBAC contract and are never collected.

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

`size_bytes` in this table is the size of `capsule.json`, not the ZIP or all managed storage. The report API's `storage` object separately reports actual `archive_bytes`, `report_bytes`, artifact `directory`, `retention_days`, and `expires_at` (cleanup eligibility based on incident capture time).

### `incident_episodes` and `episode_incidents`

Episode lifecycle, application/time correlation and membership of individually retained alert signals. Archiving affects visibility, not deletion or retention age.

### `settings`

Non-secret general configuration and runtime preferences. Provider keys are not stored in SQLite.

### `model_profiles`

Model ID, provider, enabled state, maximum tokens, and update time.

## Artifact Policy

### Episode Investigation

`state_dir/investigations/<sha256-episode-id>.json` stores the shared version-1 investigation.
The report API includes it as `investigation`; completed/incomplete attempts are also
copied as `episode_investigation.json` into each participating capsule.

- `episode_id`, `status`, `attempt`, timestamps and input fingerprint;
- bounded `context` with member alerts, impact and deduplicated `E...` evidence/provenance;
- `checks`: `Q...` ID, tool, bounded arguments, question, diagnostic purpose, status,
  timestamps and scrubbed source observations/limitations;
- `calls`: timestamps, provider usage, latency, finish reason, structured decision
  and any validation error (never private model deliberation);
- `assessment`: summary, likely mechanism, next action, expected finding, uncertainty,
  evidence references, hypothesis states and relationships between member alerts;
- `usage`: current attempt's prompt/completion/total token sums and completeness flag;
- `lifetime_usage`: earlier attempts' reported totals, separate from current usage;
- `previous_runs`: up to three earlier attempts;
- `source_retention`: `unknown`, distinct from managed incident cleanup.

Allowed statuses and tool boundaries are documented in [AI techniques](docs/ai_investigation_techniques.md).
Deleting a member invalidates the shared file and its surviving artifact copies.

### Archive Members

The derived ZIP contains:

- `capsule.json`;
- `capsule.md`;
- `evidence.json`;
- `evaluation.json`;
- `baselines.json`;
- `incident_report.json` when a control-plane report exists;
- `ai_briefing.json` when present;
- `episode_investigation.json` when available;
- `dashboard.html` when rendered.

Only these allowlisted files are included if present. Offline comparison prompts/responses, normalized raw inputs and raw traces are excluded. Selected log examples and retained PM trend values are still telemetry-derived content and may be sensitive.

Live normalized inputs are separately staged in `state_dir/live-cases/` until incident cleanup. External input directories remain at their original path. Artifact exclusion is not evidence that no raw data exists on the state volume.
