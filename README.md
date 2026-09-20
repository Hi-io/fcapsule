# FCAPSule

FCAPSule is a telemetry attention and incident evidence retention engine. It observes fault-management events, performance metrics, application logs, topology, and on-demand trace availability, then produces a compact evidence capsule that remains useful after raw telemetry expires.

It does not replace Prometheus, OpenSearch, Alertmanager, Kafka, or a tracing backend. FCAPSule opens a bounded incident window, selects cross-domain evidence, records its provenance, and retains an investigation report. Live capture currently also stages bounded source responses on disk; the downloadable capsule excludes those raw inputs.

**Deployment status:** functional, single-replica software for a trusted environment. The reference service has no built-in authentication or TLS and is not a hardened, Internet-facing service. See the [deployment constraints](docs/kubernetes_deployment.md#current-constraints).

## Product Surfaces

FCAPSule provides one control plane with three operator views:

- **Operations** (`/console`) groups related firing alerts into expandable incident episodes. Overview presents one evidence-seeking AI investigation per episode, a concrete next action and check progress. Evidence and Timeline expose observations and the agent's activity separately from historical events. Reports and capsules can be exported.
- **Targets** (`/targets`) configures and tests Prometheus, OpenSearch, and Kubernetes API access, controls namespace scope and polling, and shows coverage for currently observed applications.
- **Settings** (`/settings`) controls incident retention, the investigation model and completion budget per call. With a provider key configured, new reports trigger background episode investigation. Retention defaults to 30 days. The key remains local in `.env` and is never returned through the console or stored in SQLite.

The CLI remains fully usable without the web application.

Workload simulation is deliberately outside this repository. FCAPSule Lab owns its own Compose/Kubernetes workloads and failure controls. Real applications and lab workloads use the same observability sources; neither a simulator nor a database is bundled into FCAPSule.

## Operational Domains

The project uses the word *domain* for telemetry families with different data shapes and analysis methods:

| Domain | Input | FCAPSule treatment |
|---|---|---|
| Fault management (FM) | alerts and incident events | trigger, severity, event sequence, affected entities |
| Performance management (PM) | numeric time series | baseline comparison and anomaly selection |
| Application logs | semi-structured text | masking, template reduction, severity and proximity scoring |
| Topology and configuration | service relationships and runtime changes | cross-source entity alignment and dependency context |
| On-demand traces | optional case metadata | retain declared availability and source retention; a live trace-backend adapter is not implemented |
| AI reasoning | selected evidence and bounded read-only tool observations | competing explanations, alert relationships, reference comparisons and cited next actions |

These are operational modalities, not media modalities. FCAPSule does not generate or process images to satisfy multidomain behavior.

## Quickstart

Requirements: Python 3.11+. From the repository directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m fcapsule.cli serve
```

Open:

- Operations: `http://127.0.0.1:8765/console`
- Settings: `http://127.0.0.1:8765/settings`
- Targets: `http://127.0.0.1:8765/targets`

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. The control plane stores metadata, bounded live captures, reports and local settings under `.fcapsule/`, which is ignored by Git. No source is connected until configured in Targets or supplied through environment variables.

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

Model comparison is an offline evaluation workflow, not an operator dashboard. The evidence report is built deterministically. With a key configured, a background investigator preserves mutable workload state and lets the model select up to four bounded checks before concluding. Tools can query resource history, literal log matches, peer/preceding-window comparisons, namespace-scoped database metrics and unselected retained log candidates. Valid evidence references and uncertainty are required, but citation checks do not prove the explanation. No remediation or application replication is executed. See [AI investigation techniques](docs/ai_investigation_techniques.md) for scope, budgets and limitations.

Re-score stored responses after a rubric change without making provider calls:

```bash
python3 -m fcapsule.cli rescore-llms \
  --capsule ./.fcapsule/capsules/<incident-id>/capsule.json \
  --out ./.fcapsule/capsules/<incident-id>
```

## Capsule Outputs

The control-plane flow produces the artifacts below. Standalone `investigate` writes the core capsule/evaluation files; responder reports and automatic briefings belong to the control plane. Comparison artifacts are written only by explicit evaluation commands.

```text
capsule.json          structured evidence and provenance
incident_report.json  responder-focused report: impact, actions, evidence, retention
episode_investigation.json  episode context, checks, cited assessment and token usage
ai_briefing.json      legacy per-incident briefing, when already present
capsule.md            human-readable investigation capsule
evidence.json         all candidates with scoring components
evaluation.json       engineering evaluation, not the primary operator view
baselines.json        comparison baselines
dashboard.html        static detailed review
llm_comparison.json   optional same-input model comparison
llm_prompt.json       optional recorded prompt
fcapsule_<id>.zip     derived evidence, report and available investigation; no raw inputs
```

## Data and Retention Policy

- Raw telemetry is read from configured sources for a bounded incident window.
- Live responses are staged under `.fcapsule/live-cases/` until incident retention or deletion. They may contain sensitive raw logs and metric samples; protect the state volume.
- Capsule archives exclude raw input files and raw trace spans, but include selected log examples and retained metric/chart values.
- Representative log lines are masked before entering evidence. Heuristic masking is not a guarantee of complete anonymization.
- Trace availability can be supplied by an external case. The live Kubernetes integration does not yet query a trace backend.
- Model hypotheses cite captured evidence or successful check observations and remain investigation paths rather than final root-cause claims. Source expiry is unknown unless explicitly supplied; FCAPSule's cleanup policy does not describe Prometheus/OpenSearch retention.

## Tests

```bash
python3 -m unittest discover -s tests -v
node --test tests/ui_helpers.test.cjs
```

Node is needed only for frontend tests, not to run FCAPSule. The suite covers validation, processing, domain-balanced selection, hypothesis references,
model comparison scoring, external-case ingestion, SQLite control-plane state, HTTP
routes, and the full capsule pipeline.

## Deployment

The reference deployment runs as a Kubernetes pod with read-only access to Prometheus, OpenSearch, and the Kubernetes API. Prometheus firing alerts trigger bounded capture; range queries provide PM data; OpenSearch supplies Filebeat-indexed logs; and the Kubernetes API supplies workload identity, PodSpec state, and referenced ConfigMaps. Secrets are never read as configuration evidence.

Source systems remain the telemetry system of record. The pod retains bounded live captures as well as applications, incidents, reports, capsules and settings on its state volume. Retention defaults to 30 days from capture, including archived incidents; external case directories outside the managed state directory are never deleted by FCAPSule. SQLite and in-process workers are single-replica constraints. See [privacy and retention](docs/data_privacy.md).

## Documentation

Start with the [documentation index](docs/README.md), which separates current operating guides, research framing and historical evaluations. The repository is [MIT licensed](LICENSE).

- `FCAPSule_AI_Concept.md`: stable problem, purpose, and research framing.
- `FCAPSule_AI_Project_Guide.md`: current product requirements and operating boundaries.
- `PROJECT_DESIGN.md`: implemented architecture and engineering rationale.
- `DATA_SCHEMA.md`: normalized case, store, and capsule contracts.
- `EVALUATION_PLAN.md`: objective metrics and model-comparison protocol.
- `docs/operations.md`: CLI and web operating guide.
- `docs/kubernetes_deployment.md`: live-source Kubernetes deployment and verification runbook.
- `docs/external_workload_boundary.md`: boundary between FCAPSule and workloads such as FCAPSule Lab.
- `docs/architecture.md`: component and deployment architecture.
- `docs/ai_investigation_techniques.md`: tool-driven episode reasoning, preservation, reference comparisons, validation and token accounting.
- `docs/data_privacy.md`: collection, anonymization, and retention policy.
- `docs/design_decisions.md`: important design decisions and tradeoffs.
- `docs/production_product_requirements.md`: operator-first product requirements and acceptance criteria.
- `docs/llm_comparison.md`: model profiles, prompts, and scoring.
- `ROADMAP.md`: remaining work toward distributed operation.
