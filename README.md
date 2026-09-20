# FCAPSule

FCAPSule is a telemetry attention and incident evidence retention engine. It observes fault-management events, performance metrics, application logs, topology, and on-demand trace availability, then produces a compact evidence capsule that remains useful after raw telemetry expires.

It is not another root-cause chatbot and it does not replace Prometheus, OpenSearch, Alertmanager, Kafka, or a tracing backend. FCAPSule sits above those systems as an investigation layer: it opens a bounded incident window, extracts the strongest cross-domain evidence, records why each item was selected, and stores derived evidence instead of copying raw telemetry.

## Product Surfaces

FCAPSule provides one control plane with three operator views:

- **Operations** (`/console`) groups related firing alerts into expandable incident episodes. Overview prioritizes an automatic AI assessment and observed impact; Evidence and Timeline keep detailed telemetry accessible. Reports and capsules can be exported.
- **Targets** (`/targets`) configures and tests Prometheus, OpenSearch, and Kubernetes API access, controls namespace scope and polling, and shows coverage for currently observed applications.
- **Settings** (`/settings`) controls incident retention and the cited-briefing model. With a provider key configured, new reports are analysed automatically in the background. Retention defaults to 30 days. The key remains local in `.env` and is never returned through the console or stored in SQLite.

The CLI remains fully usable without the web application.

Workload simulation is deliberately outside this repository. The separate FCAPSule Lab
project runs a five-container Compose workload with
PostgreSQL, application services, traffic, Prometheus, and failure injection. It exports
a bounded normalized case for FCAPSule; it is not a product screen or runtime dependency.

## Operational Domains

The project uses the word *domain* for telemetry families with different data shapes and analysis methods:

| Domain | Input | FCAPSule treatment |
|---|---|---|
| Fault management (FM) | alerts and incident events | trigger, severity, event sequence, affected entities |
| Performance management (PM) | numeric time series | baseline comparison and anomaly selection |
| Application logs | semi-structured text | masking, template reduction, severity and proximity scoring |
| Topology and configuration | service relationships and runtime changes | cross-source entity alignment and dependency context |
| On-demand traces | temporary source buffer | availability and retention-window probe; raw spans are not retained |
| AI reasoning | selected evidence only | cited investigation paths, limitations, and next checks |

These are operational modalities, not media modalities. FCAPSule does not generate or process images to satisfy multidomain behavior.

## Quickstart

Requirements: Python 3.11+ and PyYAML.

```bash
python3 -m fcapsule.cli serve
```

Open:

- Operations: `http://127.0.0.1:8765/console`
- Settings: `http://127.0.0.1:8765/settings`
- Targets: `http://127.0.0.1:8765/targets`

The control plane stores local metadata under `.fcapsule/`. That directory is ignored by Git.

### Run in Kubernetes with live sources

FCAPSule can run inside a cluster and discover workloads through its mounted ServiceAccount. The provided deployment connects to Prometheus and OpenSearch over their in-cluster service names and reads pods plus referenced ConfigMaps through read-only RBAC.

```bash
kubectl apply -f deploy/kubernetes/local-single-node-storage.yaml
kubectl apply -f deploy/kubernetes/prometheus-rule.yaml
kubectl apply -f deploy/kubernetes/fcapsule.yaml
```

For development, apply `deploy/kubernetes/dev-overlay.yaml` to the Deployment after the base manifest. It installs the current `master` source into an `emptyDir`, so an iteration only requires a push and `kubectl rollout restart deployment/fcapsule -n fcapsule`.

The default NodePort is `http://<node-ip>:30765`. Edit the `fcapsule-runtime` ConfigMap or use **Targets** to change URLs, namespaces, polling, and incident-window settings. See `docs/kubernetes_deployment.md` for RBAC, secrets, storage, verification, and production notes.

### Ingest an externally captured case

```bash
python3 -m fcapsule.cli ingest-case \
  --case /path/to/normalized-case \
  --app-id payments-api \
  --app-name "Payments API"
```

`ingest-case` validates the normalized case and records its metadata without copying raw
telemetry into FCAPSule storage. In Operations, select **Build report** for the captured
incident. FCAPSule does not ship an operator-facing sample case. The synthetic fixture
used by the test suite is isolated under `tests/fixtures/`.

### Build a capsule directly

```bash
python3 -m fcapsule.cli investigate \
  --case /path/to/normalized-case \
  --out ./.fcapsule/capsules/<incident-id>
```

### Register an application

```bash
python3 -m fcapsule.cli register \
  --app-id checkout-platform \
  --name "Checkout Platform" \
  --namespace commerce \
  --cluster local-compose \
  --environment development
```

### Inspect control-plane state

```bash
python3 -m fcapsule.cli status
```

### Evaluate models offline

Place `DEEPSEEK_API_KEY=...` in a local `.env` file. `.env` is ignored by Git.

```bash
python3 -m fcapsule.cli compare-llms \
  --capsule ./.fcapsule/capsules/<incident-id>/capsule.json \
  --out ./.fcapsule/capsules/<incident-id> \
  --models deepseek-v4-flash deepseek-v4-pro
```

Model comparison is an offline evaluation workflow. It does not run in the operator path and does not delay an incident report. The production report is built from deterministic detection, selected evidence, verified hypotheses, and explicit uncertainty. Operators may request an optional DeepSeek Pro briefing after the report is ready; it is retained only when it cites existing evidence IDs and states an uncertainty.

Re-score stored responses after a rubric change without making provider calls:

```bash
python3 -m fcapsule.cli rescore-llms \
  --capsule ./.fcapsule/capsules/<incident-id>/capsule.json \
  --out ./.fcapsule/capsules/<incident-id>
```

## Capsule Outputs

An investigation writes:

```text
capsule.json          structured evidence and provenance
incident_report.json  responder-focused report: impact, actions, evidence, retention
ai_briefing.json      optional citation-checked operator briefing; no prompt retained
capsule.md            human-readable investigation capsule
evidence.json         all candidates with scoring components
evaluation.json       engineering evaluation, not the primary operator view
baselines.json        comparison baselines
dashboard.html        static detailed review
llm_comparison.json   optional same-input model comparison
llm_prompt.json       optional recorded prompt
fcapsule_<id>.zip     derived evidence only; no raw telemetry
```

## Data and Retention Policy

- Raw telemetry is read from configured sources for a bounded incident window.
- Capsule archives exclude raw logs and raw trace spans.
- Representative log lines are anonymized before entering evidence.
- Trace integrations are query-on-demand. FCAPSule records availability, source retention, and derived findings, not the underlying span set.
- Every generated hypothesis must cite selected evidence IDs and remains an investigation path rather than a final root-cause claim.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite covers validation, processing, domain-balanced selection, hypothesis grounding,
model comparison scoring, external-case ingestion, SQLite control-plane state, HTTP
routes, and the full capsule pipeline.

## Deployment

The reference deployment runs as a Kubernetes pod with read-only access to Prometheus, OpenSearch, and the Kubernetes API. Prometheus firing alerts trigger bounded capture; range queries provide PM data; OpenSearch supplies Filebeat-indexed logs; and the Kubernetes API supplies workload identity, PodSpec state, and referenced ConfigMaps. Secrets are never read as configuration evidence.

Raw telemetry remains in the source systems. The pod retains application registrations, incident metadata, responder reports, capsules, evaluation results, and source references on its state volume. SQLite and in-process workers remain single-replica constraints; PostgreSQL, object storage, durable jobs, authentication, and multi-cluster control are later scaling work.

## Documentation

- `FCAPSule_AI_Concept.md`: stable problem, purpose, and research framing.
- `FCAPSule_AI_Project_Guide.md`: current product requirements and operating boundaries.
- `PROJECT_DESIGN.md`: implemented architecture and engineering rationale.
- `DATA_SCHEMA.md`: normalized case, store, and capsule contracts.
- `EVALUATION_PLAN.md`: objective metrics and model-comparison protocol.
- `docs/operations.md`: CLI and web operating guide.
- `docs/kubernetes_deployment.md`: live-source Kubernetes deployment and verification runbook.
- `docs/external_workload_boundary.md`: boundary between FCAPSule and workloads such as FCAPSule Lab.
- `docs/architecture.md`: component and deployment architecture.
- `docs/data_privacy.md`: collection, anonymization, and retention policy.
- `docs/design_decisions.md`: important design decisions and tradeoffs.
- `docs/production_product_requirements.md`: operator-first product requirements and acceptance criteria.
- `docs/llm_comparison.md`: model profiles, prompts, and scoring.
- `ROADMAP.md`: remaining work toward distributed operation.
