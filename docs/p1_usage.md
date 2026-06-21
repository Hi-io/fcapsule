# P1 Usage Guide

## Validate and Inspect

```bash
python3 -m fcapsule.cli inspect --case cases/case_001
```

This prints metadata, counts, matched entities, coverage, and warnings without writing an output capsule.

## Investigate

```bash
python3 -m fcapsule.cli investigate --case cases/case_001 --out outputs/case_001
```

Successful execution prints source counts, selected evidence, verified hypotheses, evaluation metrics, and the archive path.

## Evaluate Again

```bash
python3 -m fcapsule.cli evaluate --case cases/case_001 --output outputs/case_001
```

Evaluation reruns the deterministic pipeline so metrics remain synchronized with implementation changes.

## Recreate the Reference Incident

```bash
python3 scripts/capture_demo_incident.py --output cases/case_001
```

The script:

1. starts a local HTTP checkout service on an ephemeral loopback port;
2. confirms its health endpoint;
3. sends healthy checkout traffic;
4. enables a deliberate payment-dependency failure;
5. observes HTTP 503 responses and structured ERROR/WARN logs;
6. snapshots service metrics after each request;
7. evaluates the configured `request_error_rate > 0.25` alert rule;
8. fails without writing a successful result if the alert does not fire;
9. writes and validates the complete case folder.

## Troubleshooting

- `Case validation failed`: read the field-specific message and compare input with `DATA_SCHEMA.md`.
- Missing PyYAML: install `requirements.txt` in a Python environment.
- No alert during capture: verify the failure request count is sufficient to exceed 25% error rate.
- Unexpected anomaly results: confirm counter metrics end in `_total` and timestamps cover both baseline and incident periods.
