# Operations Guide

## Start the Service

```bash
python3 -m fcapsule.cli serve
```

Default URLs:

- `http://127.0.0.1:8765/console`
- `http://127.0.0.1:8765/lab`

Use `--host`, `--port`, and `--state-dir` to change the binding or storage location.

## Operations View

The Operations view shows:

- an incident queue ordered by severity and capture time;
- an **Open report** action for each incident with a retained capsule;
- incident impact, a verified investigation path, uncertainty, and concrete next checks;
- FM sequence, PM changes, representative evidence, topology, and trace-source retention context;
- application coverage across FM, PM, logs, and trace access.

Compression, signal preservation, grounding, runtime, and model-evaluation details are available only inside the collapsed engineering diagnostics section of a report. They support maintainers and evaluation work; they are not the first information presented to an on-call responder.

## Incident Lab

1. Set the application name and identifier.
2. Select the failure scenario.
3. Choose baseline requests, incident requests, and concurrency.
4. Select **Run simulation**.
5. Watch services, baseline, injection, and FM stages update.
6. Review the alert sequence and captured volume.
7. Select **Build capsule**.
8. Open Operations and select **Open report** to inspect the retained result.

Simulation and capsule generation are deliberately separate. This makes the boundary between source telemetry and FCAPSule processing visible.

## CLI

### Simulate

```bash
python3 -m fcapsule.cli simulate --output .fcapsule/cases/cli-latest
```

### Investigate

```bash
python3 -m fcapsule.cli investigate \
  --case .fcapsule/cases/cli-latest \
  --out .fcapsule/capsules/cli-latest
```

### Inspect a normalized case

```bash
python3 -m fcapsule.cli inspect --case .fcapsule/cases/cli-latest
```

### Register an application

```bash
python3 -m fcapsule.cli register \
  --app-id checkout-platform \
  --name "Checkout Platform" \
  --namespace commerce \
  --cluster local-lab
```

### Control-plane status

```bash
python3 -m fcapsule.cli status
```

### Evaluate models offline

```bash
python3 -m fcapsule.cli compare-llms \
  --capsule .fcapsule/capsules/cli-latest/capsule.json \
  --out .fcapsule/capsules/cli-latest
```

## Model Evaluation

Model comparison is an offline evaluation activity. It is not part of capsule capture and does not delay the operator report. Credentials are never stored in SQLite.

Place the provider key in `.env`:

```text
DEEPSEEK_API_KEY=...
```

Capsule creation always completes with deterministic detection and evidence-grounded reasoning, whether or not a provider key is available. A configured model may be evaluated or added later as a non-blocking enrichment.

## Local Files

```text
.fcapsule/
  fcapsule.db
  cases/<incident-id>/
  capsules/<incident-id>/
```

The directory is ignored by Git. To preserve results outside local development, back up capsule artifacts and the metadata database according to the deployment retention policy.

## Troubleshooting

- **Port already in use:** start with `--port 8766`.
- **Simulation does not alert:** use at least 20 incident requests and recommended concurrency 16 or higher.
- **Capsule unavailable:** complete a simulation before selecting **Build capsule**.
- **Trace shows zero retained spans:** this is expected; only source availability is retained.
