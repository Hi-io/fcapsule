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

## Compare DeepSeek Models

Set the API key only in the shell environment. Do not place it in committed files.

```bash
export DEEPSEEK_API_KEY=...
python3 -m fcapsule.cli compare-llms \
  --capsule outputs/case_001/capsule.json \
  --out outputs/case_001 \
  --models deepseek-v4-flash deepseek-v4-pro
```

The command sends the same capsule to both models and writes:

- `llm_comparison.json`;
- `llm_prompt.json`;
- `dashboard.html`.

The comparison records model output, usage, latency, citation validity, domain coverage, expected signal coverage, actionability, winner, and score delta. It does not write the API key.

## Open the Dashboard

```bash
python3 -m fcapsule.cli dashboard --output outputs/case_001
```

Open `outputs/case_001/dashboard.html` in a browser to inspect the multidomain evidence map, objective metrics, and model comparison.

## Run the Optional Demo UI

```bash
python3 -m fcapsule.cli demo-ui --case cases/case_001 --out outputs/case_001
```

The command starts a local server at `http://127.0.0.1:8765/`. The UI is optional and only reads or writes local P1 files. It provides:

- a one-screen run summary;
- buttons to rerun P1 on the current case;
- a button to capture a fresh synthetic failure and rerun P1;
- a button to rerun the DeepSeek same-input comparison when `DEEPSEEK_API_KEY` is set;
- cards for compression, signal preservation, grounded claims, and the model winner;
- tables for telemetry domains and model comparison.

Stop it with `Ctrl+C` in the terminal.

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
- Empty DeepSeek content: increase `--max-tokens`; these models may spend some completion tokens on reasoning before final content.
