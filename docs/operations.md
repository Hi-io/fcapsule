# Operations Guide

## Start the Service

```bash
python3 -m fcapsule.cli serve
```

Default URLs:

- `http://127.0.0.1:8765/console`
- `http://127.0.0.1:8765/settings`
- `http://127.0.0.1:8765/targets`
- `http://127.0.0.1:8765/patterns`

Use `--host`, `--port`, and `--state-dir` to change the binding or storage location.

## Operations View

The Operations view treats an incident episode, not an individual alert notification, as the operator's unit of work. Only firing Prometheus alerts open work. Signals for the same application within a 15-minute correlation window join one episode; later signals remain individually auditable and can retain their own reports.

An active episode takes its title and severity from a currently firing signal, not
an older resolved critical alert. Once resolved, its highest-severity historical
signal represents the episode. Time grouping is a navigation aid, not evidence that
consecutive failures share a root cause.

The view shows:

- an episode queue ordered by latest observed activity, so a newly received
  member returns its existing episode to the top even when the source alert
  carries an earlier timestamp;
- an **In queue / Archived** view selector; the unarchived queue contains active
  and resolved episodes, sorted together by latest activity;
- severity and lifecycle state, affected resource, latest signal time, related-signal count, and report readiness; node alerts prefer the captured node-exporter Pod's Kubernetes node over collector or IP labels;
- a compact recurrence badge when the same application, affected resource and
  normalized alert identity occurred earlier in retained history;
- URL-persisted namespace and text/short-reference filters, with resource,
  lifecycle and age filters in an expandable panel. Applied restrictions remain
  visible below the controls;
- a clickable episode row that expands its investigation, with an alert selector when several reports are available;
- a stable episode reference and direct-link control for sharing the selected
  episode; individual alert captures have separate references in detail views;
- archive and restore controls that keep an episode out of the active queue without deleting its signals or evidence;
- incident impact, a cited investigation path, uncertainty, and concrete next checks;
- FM sequence, PM changes, representative evidence, topology, and trace-source retention context;

Overview presents one episode assessment, next action, uncertainty and a compact investigation status. When an earlier deterministic match exists, the assessment may include a cited **Related history** comparison. It compares captured evidence; it does not establish that two events share a root cause. Additional findings, competing explanations, alert relationships and detailed checks are expandable. Evidence includes agent observations plus the selected alert capture's telemetry; the alert selector appears in detail views, not the shared Overview. Timeline separates historical alerts from later agent activity. References open the cited observation, with a return control to restore the assessment. At most two cited performance charts appear in Overview; full charts remain in Evidence. New reports record the baseline reference window, while older captures may only have a median value. Charts describe the captured interval, not current workload health, and the dashed marker is not the alert time. Queue ages are relative with exact timestamps on hover. Export names the selected capture and episode-wide investigation scope. Compression, preservation, grounding and runtime remain in collapsed engineering diagnostics.

Reports use a centered reading area: the likely explanation and next check sit
alongside each other when space allows, then stack on smaller screens. **Capture
context** preserves the initial summary without repeating it in the first view.
**Assessment history** appears independently when there are revisions but no
operator attachments; **Operator evidence** is reserved for actual supplied files.

## Patterns View

Patterns is historical context, not a second incident queue. **Recurring issues**
lists groups with at least two retained episodes using the same application,
resource and normalized alert identity. A row shows the affected resource,
occurrence count, first/most-recent observation and median observed gap, then
links to individual episodes. **Shared conditions** shows a separate, tentative
correlation view and preserves the ability to keep an episode separate from a
suggested group. Archived episodes still count until FCAPSule retention
permanently removes them. A gap describes retained history; it is not a
forecast or proof of a common failure mechanism.

## Targets View

Targets is the live-source control surface. It shows connection health for Prometheus, OpenSearch, and the Kubernetes API and lets an operator change source URLs, the OpenSearch index pattern, cluster identity, namespace scope, poll interval, incident window, and automatic report generation. **Test connections** checks saved settings without erasing unsaved edits; save changes before testing them. **Sync now** performs immediate discovery and alert polling.

For high-volume workloads, the OpenSearch capture budget is alert-focused. One quarter is reserved for the newest records immediately before the alert and three quarters for records from the alert onward. This preserves a small behavioral baseline without allowing routine traffic at the beginning of the incident window to displace the failure evidence.

Application coverage is shown on Targets, grouped by cluster and namespace. Expand an application's pod count for pod-level details. Discovery updates without replacing unsaved connection settings. Workloads that disappear are omitted from current coverage while their historical incidents remain available.

## Settings View

Settings combines lifecycle and optional AI configuration. Incident retention defaults to 30 days and accepts values from 1 to 3650 days. FCAPSule permanently removes expired active or archived incidents, their metadata, and managed report/capsule files. The age is measured from the time FCAPSule captured the incident, so importing an older event does not immediately discard it.

Archiving is separate from retention: it hides an episode from the active queue but preserves all related signals, reports, and capsules. Archived episodes can be restored or permanently deleted from the archived view.

The optional Evidence models section configures a visual specialist and audio transcription specialist for **operator-supplied** files. It remains disabled until the core investigator and the matching specialist model have passed their bounded validation. A ready file can create a new assessment revision; it never overwrites the earlier assessment. See [optional multimodal evidence](multimodal_evidence.md) for data handling and evaluation scope.

For Kubernetes installation and source prerequisites, use `docs/kubernetes_deployment.md`.

### Automatic AI Assessment

When a provider key is configured, retaining member reports queues one episode investigation. Two workers run these jobs without blocking capture. The default investigator preserves mutable workload state, lets the model select one read-only check, then produces and reviews a cited assessment. Operators can raise the bounded check count to four in Settings. Overview refreshes progress while the model works. A missing key points to Settings; an inconclusive attempt retains observations, states that no cause is asserted, and offers Reassess. Startup resumes interrupted unarchived work; failed unchanged attempts are not automatically retried. New members trigger a fresh joint assessment after their reports are ready.

The request includes retained alerts/rules, performance findings, log examples, configuration and subsequent check observations. The final response contains a likely mechanism, one next action, expected finding, uncertainty, competing hypotheses and alert relationships. For a recurring episode, it must inspect one bounded prior capsule and explicitly classify the historical comparison as similar, changed, or still insufficiently supported. The prior episode can include an **Earlier hypothesis, not proof** label; that is model prose from the earlier run and is never citable evidence. Citation validation confirms references exist, not causal truth. No remediation or application replication is executed. `episode_investigation.json` includes checks, assessment, usage and up to three previous attempts; it is copied to participating capsule archives on completion. Expand the token count for provider usage and the safety reserve. See [techniques and limits](ai_investigation_techniques.md).

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
background evidence job to finish, and then expand the episode.

FCAPSule Lab is a separate workload project for source-adapter validation and demonstrations, including Kubernetes scenarios. It is not served by FCAPSule or required in deployment.

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

## AI Investigation Settings

Open **Settings** in the console to choose the DeepSeek-compatible model and
completion budget per call for the optional episode investigator. Paste a replacement API key only when
necessary. It is saved to the configured state directory's `.env` file and never returned to the browser or
written to SQLite. The non-secret selection is persisted in `.fcapsule/ai-settings.json`.

## Local Files

```text
.fcapsule/
  .env
  fcapsule.db
  ai-settings.json
  source-settings.json
  investigations/<episode-hash>.json
  live-cases/<incident-id>/
  capsules/<incident-id>/
```

The directory is ignored by Git. `live-cases/` contains bounded raw captures, not only summaries. Managed incident deletion/retention removes these inputs and capsule artifacts. External input directories outside managed state are not removed. See [privacy and retention](data_privacy.md) before backing up or sharing the volume.

## Export and Saved Reports

Expand an episode and choose **Export**. Download the capsule ZIP for the retained evidence/analysis bundle or the report JSON for structured incident details. The menu shows actual file sizes, the cleanup eligibility date and an expandable server-side storage path. It is not a full observability backup. Existing downloaded copies are not removed by FCAPSule retention.

Current-format reports do not need the original source directory to open. If an old report needs rebuilding after its source disappears, only retained capsule evidence is available; missing raw time-series samples cannot be reconstructed. Trace availability in an imported case is historical context, not proof of a currently reachable trace backend.

Cleanup is checked while control-plane snapshots are served, at most once per minute. Archiving does not reset capture time or extend retention. The displayed date is eligibility, not a guarantee of deletion at that exact second.

## Troubleshooting

- **Port already in use:** start with `--port 8766`.
- **No incidents appear:** check Targets, namespace scope, and active Prometheus alerts; external cases can also be ingested manually.
- **Pod is visible but logs are waiting:** confirm Filebeat has indexed recent documents with `kubernetes.namespace` and `kubernetes.pod.name` keyword fields.
- **Kubernetes target fails:** verify the ServiceAccount token/CA mount and the `fcapsule-observer` ClusterRoleBinding.
- **Build report is unavailable:** wait for the active capsule job to finish before starting another one.
- **Trace shows zero retained spans:** this is expected; only source availability is retained.
