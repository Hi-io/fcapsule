# Operations Guide

## Start the Service

```bash
python3 -m fcapsule.cli serve
```

Default URLs:

- `http://127.0.0.1:8765/console`
- `http://127.0.0.1:8765/settings`

Use `--host`, `--port`, and `--state-dir` to change the binding or storage location.

## Operations View

The Operations view shows:

- an incident queue ordered by severity and capture time;
- an **Open report** action for each incident with a retained capsule; the report expands directly below that queue row;
- incident impact, a verified investigation path, uncertainty, and concrete next checks;
- FM sequence, PM changes, representative evidence, topology, and trace-source retention context;
- application coverage across FM, PM, logs, and trace access.

The report distinguishes its evidence by domain: FM alert records, PM trend lines with normal/peak values, and expandable anonymized log patterns. Compression, signal preservation, grounding, runtime, and model-evaluation details are available only inside the collapsed engineering diagnostics section. They support maintainers and evaluation work; they are not the first information presented to an on-call responder.

### Optional AI Briefing

After a report is ready, **Generate AI briefing** requests a compact DeepSeek Pro second reading. The request contains only curated incident evidence. A response is displayed and retained only when it cites two to five evidence IDs already present in the report and acknowledges an uncertainty. This action does not block capture, capsule creation, or the report. The resulting `ai_briefing.json` is added to the archive without storing provider prompts or transcripts.

## Ingest an External Incident

Use `ingest-case` when a source adapter, export job, or workload lab has prepared a
normalized bounded case directory:

```bash
python3 -m fcapsule.cli ingest-case \
  --case /path/to/normalized-case \
  --app-id payments-api \
  --app-name "Payments API"
```

The command validates the case and records metadata without copying raw telemetry into
`.fcapsule`. In Operations, select **Build report** for that incident, wait for the
background evidence job to finish, and then select **Open report**.

FCAPSule Lab is a separate Docker Compose project for local source-adapter validation
and demonstrations. It is not served by FCAPSule or required in deployment.

## CLI

### Investigate

```bash
python3 -m fcapsule.cli investigate \
  --case /path/to/normalized-case \
  --out .fcapsule/capsules/<incident-id>
```

### Inspect a normalized case

```bash
python3 -m fcapsule.cli inspect --case /path/to/normalized-case
```

### Register an application

```bash
python3 -m fcapsule.cli register \
  --app-id checkout-platform \
  --name "Checkout Platform" \
  --namespace commerce \
  --cluster local-compose
```

### Control-plane status

```bash
python3 -m fcapsule.cli status
```

### Evaluate models offline

```bash
python3 -m fcapsule.cli compare-llms \
  --capsule .fcapsule/capsules/<incident-id>/capsule.json \
  --out .fcapsule/capsules/<incident-id>
```

## Model Evaluation

Model comparison is an offline evaluation activity. It is not part of capsule capture and does not delay the operator report. Credentials are never stored in SQLite.

Place the provider key in `.env`:

```text
DEEPSEEK_API_KEY=...
```

Capsule creation always completes with deterministic detection and evidence-grounded reasoning, whether or not a provider key is available. A configured model may be evaluated or added later as a non-blocking enrichment.

## AI Settings

Open **AI settings** in the console to choose the DeepSeek-compatible model and
completion budget for an optional cited briefing. Paste a replacement API key only when
necessary. It is saved to the local `.env` file and never returned to the browser or
written to SQLite. The non-secret selection is persisted in `.fcapsule/ai-settings.json`.

## Local Files

```text
.fcapsule/
  fcapsule.db
  ai-settings.json
  capsules/<incident-id>/
```

The directory is ignored by Git. To preserve results outside local development, back up capsule artifacts and the metadata database according to the deployment retention policy.

## Troubleshooting

- **Port already in use:** start with `--port 8766`.
- **No incidents appear:** validate and ingest a normalized external case first.
- **Build report is unavailable:** wait for the active capsule job to finish before starting another one.
- **Trace shows zero retained spans:** this is expected; only source availability is retained.
