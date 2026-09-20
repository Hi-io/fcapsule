# Operations Guide

## Start the Service

```bash
python3 -m fcapsule.cli serve
```

Default URLs:

- `http://127.0.0.1:8765/console`
- `http://127.0.0.1:8765/settings`
- `http://127.0.0.1:8765/targets`

Use `--host`, `--port`, and `--state-dir` to change the binding or storage location.

## Operations View

The Operations view shows:

- an incident queue ordered by severity and capture time;
- an **Open report** action for each incident with a retained capsule; the report expands directly below that queue row;
- archive and restore controls that keep resolved incidents out of the active queue without deleting evidence;
- incident impact, a verified investigation path, uncertainty, and concrete next checks;
- FM sequence, PM changes, representative evidence, topology, and trace-source retention context;

The report distinguishes its evidence by domain: FM alert records, PM trend lines with normal/peak values, and expandable anonymized log patterns. Compression, signal preservation, grounding, runtime, and model-evaluation details are available only inside the collapsed engineering diagnostics section. They support maintainers and evaluation work; they are not the first information presented to an on-call responder.

## Targets View

Targets is the live-source control surface. It shows connection health for Prometheus, OpenSearch, and the Kubernetes API and lets an operator change source URLs, the OpenSearch index pattern, cluster identity, namespace scope, poll interval, incident window, and automatic report generation. **Test connections** checks credentials and reachability without changing settings; **Sync now** performs immediate discovery and alert polling.

Application coverage is shown on Targets because it describes current source discovery rather than incident state. A discovered workload is mapped by cluster, namespace, workload, and pod. Workloads that disappear are omitted from current coverage while their historical incidents remain available.

## Settings View

Settings combines lifecycle and optional AI configuration. Incident retention defaults to 30 days and accepts values from 1 to 3650 days. FCAPSule permanently removes expired active or archived incidents, their metadata, and managed report/capsule files. The age is measured from the time FCAPSule captured the incident, so importing an older event does not immediately discard it.

Archiving is separate from retention: it hides an incident from the active queue but preserves the report and capsule. Archived incidents can be restored or permanently deleted from the archived view.

For Kubernetes installation and source prerequisites, use `docs/kubernetes_deployment.md`.

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

## AI Briefing Settings

Open **Settings** in the console to choose the DeepSeek-compatible model and
completion budget for an optional cited briefing. Paste a replacement API key only when
necessary. It is saved to the configured state directory's `.env` file and never returned to the browser or
written to SQLite. The non-secret selection is persisted in `.fcapsule/ai-settings.json`.

## Local Files

```text
.fcapsule/
  fcapsule.db
  ai-settings.json
  source-settings.json
  live-cases/<incident-id>/
  capsules/<incident-id>/
```

The directory is ignored by Git. To preserve results outside local development, back up capsule artifacts and the metadata database according to the deployment retention policy.

## Troubleshooting

- **Port already in use:** start with `--port 8766`.
- **No incidents appear:** check Targets, namespace scope, and active Prometheus alerts; external cases can also be ingested manually.
- **Pod is visible but logs are waiting:** confirm Filebeat has indexed recent documents with `kubernetes.namespace` and `kubernetes.pod.name` keyword fields.
- **Kubernetes target fails:** verify the ServiceAccount token/CA mount and the `fcapsule-observer` ClusterRoleBinding.
- **Build report is unavailable:** wait for the active capsule job to finish before starting another one.
- **Trace shows zero retained spans:** this is expected; only source availability is retained.
