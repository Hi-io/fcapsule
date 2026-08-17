# FCAPSule

FCAPSule is a telemetry attention and incident evidence retention engine. It observes fault-management events, performance metrics, application logs, topology, and on-demand trace availability, then produces a compact evidence capsule that remains useful after raw telemetry expires.

It is not another root-cause chatbot and it does not replace Prometheus, OpenSearch, Alertmanager, Kafka, or a tracing backend. FCAPSule sits above those systems as an investigation layer: it opens a bounded incident window, extracts the strongest cross-domain evidence, records why each item was selected, and stores derived evidence instead of copying raw telemetry.

## Product Surfaces

FCAPSule provides one local control plane with two web views:

- **Operations** (`/console`) lists tracked applications, signal-source status, incidents, retained capsules, storage reduction, and model profiles.
- **Incident Lab** (`/lab`) runs a controlled multi-service failure and shows each stage as it happens. It exists for testing, demonstrations, and regression evaluation; it is not required for normal capsule generation.

The CLI remains fully usable without the web application.

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
- Incident Lab: `http://127.0.0.1:8765/lab`

The control plane stores local metadata under `.fcapsule/`. That directory is ignored by Git.

### Run the incident from the CLI

```bash
python3 -m fcapsule.cli simulate \
  --output ./.fcapsule/cases/cli-latest \
  --baseline-requests 180 \
  --incident-requests 240 \
  --concurrency 24
```

The scenario starts live checkout and inventory HTTP services. A runtime configuration change creates inventory partition lock contention. Checkout retries continue while the circuit breaker remains closed, amplifying dependency calls until database-pool saturation and user-facing failures trigger three alerts.

### Build a capsule directly

```bash
python3 -m fcapsule.cli investigate \
  --case ./.fcapsule/cases/cli-latest \
  --out ./.fcapsule/capsules/cli-latest
```

### Register an application

```bash
python3 -m fcapsule.cli register \
  --app-id checkout-platform \
  --name "Checkout Platform" \
  --namespace commerce \
  --cluster local-lab \
  --environment development
```

### Inspect control-plane state

```bash
python3 -m fcapsule.cli status
```

### Compare configured models

Place `DEEPSEEK_API_KEY=...` in a local `.env` file. `.env` is ignored by Git.

```bash
python3 -m fcapsule.cli compare-llms \
  --capsule ./.fcapsule/capsules/cli-latest/capsule.json \
  --out ./.fcapsule/capsules/cli-latest \
  --models deepseek-v4-flash deepseek-v4-pro
```

Model comparison is optional. The deterministic evidence selector and hypothesis verifier work without an API key.

Re-score stored responses after a rubric change without making provider calls:

```bash
python3 -m fcapsule.cli rescore-llms \
  --capsule ./.fcapsule/capsules/cli-latest/capsule.json \
  --out ./.fcapsule/capsules/cli-latest
```

## Incident Lab Output

The default lab workload produces:

- two live local services and concurrent HTTP traffic;
- a healthy baseline followed by a controlled degradation;
- thousands of structured logs from checkout and inventory components;
- more than twenty PM series;
- a three-stage FM alert sequence;
- topology and configuration-change context;
- a verified on-demand trace probe with zero raw spans retained;
- an evidence capsule, objective evaluation, dashboard, and derived-only archive.

The exact counts vary slightly with thread scheduling. The causal structure and required signal groups are deterministic and covered by regression tests.

## Capsule Outputs

An investigation writes:

```text
capsule.json          structured evidence and provenance
capsule.md            human-readable investigation capsule
evidence.json         all candidates with scoring components
evaluation.json       reduction, preservation, grounding, and runtime
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

The suite covers validation, processing, domain-balanced selection, hypothesis grounding, model comparison scoring, the real incident simulation, SQLite control-plane state, HTTP routes, and the full capsule pipeline.

## Deployment Direction

The local control plane is the reference implementation. The intended deployment model is a service or Kubernetes pod configured with read-only access to observability APIs and durable metadata storage. Adapters normalize OpenSearch, Prometheus, Alertmanager, topology, and trace-source responses into the same incident contract used by the local lab.

Raw telemetry remains in the source systems. The pod retains application registrations, incident metadata, capsules, evaluation results, and source references. See `docs/architecture.md` and `ROADMAP.md` for the distributed path.

## Documentation

- `FCAPSule_AI_Concept.md`: stable problem, purpose, and research framing.
- `FCAPSule_AI_Project_Guide.md`: current product requirements and operating boundaries.
- `PROJECT_DESIGN.md`: implemented architecture and engineering rationale.
- `DATA_SCHEMA.md`: normalized case, store, and capsule contracts.
- `EVALUATION_PLAN.md`: objective metrics and model-comparison protocol.
- `docs/operations.md`: CLI and web operating guide.
- `docs/architecture.md`: component and deployment architecture.
- `docs/data_privacy.md`: collection, anonymization, and retention policy.
- `docs/design_decisions.md`: important design decisions and tradeoffs.
- `docs/llm_comparison.md`: model profiles, prompts, and scoring.
- `ROADMAP.md`: remaining work toward distributed operation.
