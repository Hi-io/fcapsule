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

- registered applications and current state;
- FM, PM, log, and trace source availability;
- incident and capsule counts;
- raw bytes inspected and capsule bytes retained;
- capsule reduction, preservation, and model result;
- model enablement and maximum-token settings;
- strongest selected evidence for a chosen capsule;
- links to Markdown and static dashboard artifacts.

## Incident Lab

1. Set the application name and identifier.
2. Select the failure scenario.
3. Choose baseline requests, incident requests, and concurrency.
4. Select **Run simulation**.
5. Watch services, baseline, injection, and FM stages update.
6. Review the alert sequence and captured volume.
7. Select **Build capsule**.
8. Watch evidence processing and optional model stages.
9. Open Operations to inspect the retained result.

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

### Compare models

```bash
python3 -m fcapsule.cli compare-llms \
  --capsule .fcapsule/capsules/cli-latest/capsule.json \
  --out .fcapsule/capsules/cli-latest
```

## Model Configuration

The web UI stores enabled state and maximum tokens in SQLite. Credentials are never stored there.

Place the provider key in `.env`:

```text
DEEPSEEK_API_KEY=...
```

When no key is available, capsule creation completes with deterministic reasoning and records the model stage as skipped.

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
- **No model result:** verify `.env`, restart the service, and confirm the model is enabled.
- **Simulation does not alert:** use at least 20 incident requests and recommended concurrency 16 or higher.
- **Capsule unavailable:** complete a simulation before selecting **Build capsule**.
- **Trace shows zero retained spans:** this is expected; only source availability is retained.

