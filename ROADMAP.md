# FCAPSule Roadmap

FCAPSule provides a single-replica Kubernetes control plane, live Prometheus/OpenSearch/Kubernetes adapters, domain-balanced evidence capsules, model profiles, and four operational web views. The remaining roadmap focuses on hardening, additional integrations, scale, and broader evaluation.

## 1. Source Hardening and Expansion

- Alertmanager webhook receiver and alert query support;
- Tempo, Jaeger, or OpenTelemetry trace availability and on-demand query;
- Kafka/OpenSearch indexing-lag metadata;
- bearer token, TLS client certificate, and external Secret references;
- retries, backoff, circuit breaking, pagination, and source rate limits;
- multi-cluster target registry and per-source query diagnostics.
- independent staging TTL after successful capture, with explicit rebuild guarantees.

## 2. Service Deployment

- FastAPI or equivalent production HTTP boundary;
- background job queue;
- PostgreSQL metadata;
- object storage for immutable capsule artifacts;
- Helm chart and environment-specific overlays;
- internal FCAPSule Prometheus metrics endpoint;
- role-based access and audit logging;
- integrity hashes, backup/restore verification and per-application retention policies (global retention exists).

## 3. Evidence Quality

- calibrated scoring weights from reviewed cases;
- semantic log embeddings as an optional model;
- stronger contradiction handling;
- configuration/deployment change correlation;
- semantic similarity beyond deterministic recurrence matching;
- explicit evidence exclusion reasons in the UI;
- reviewer feedback and relevance labels.

## 4. AI Orchestration

Implemented: joint episode assessment, fixed read-only tool dispatch, incremental
observation retention, peer/preceding-window comparisons, omitted-log inspection,
bounded retained-history comparison, competing hypotheses, bounded calls and
provider-reported token usage. These are
described in [AI techniques](docs/ai_investigation_techniques.md). Remaining work:

- provider-neutral model registry;
- additional pretrained LLM comparisons;
- pretrained time-series methods compared with the statistical baseline;
- semantic log model comparison;
- cost budgets and rate limiting;
- prompt and rubric versioning;
- model result history and reviewer preference.

## 5. Operations Experience

- application registration editor;
- source query history and detailed diagnostics;
- pattern suppression/ownership after review;
- capsule comparison and version history;
- authenticated sharing and portable human-readable export (JSON/ZIP downloads and local incident URLs exist);
- reviewer notes;
- configurable evidence budgets; global incident retention is already available in Settings.

## 6. Evaluation

- qualification of the remaining complex Lab scenarios beyond the three current demo cases;
- anonymized real incident windows where approval exists;
- method and model ablations;
- domain-expert usefulness review;
- latency, cost, and scale curves;
- unsupported-claim and faithfulness analysis;
- documented threats to validity.

## 7. Distributed Scale

- queue-backed workers;
- idempotent alert-trigger jobs;
- sharded application ownership;
- event-driven scheduling;
- multi-cluster registry;
- high-availability metadata and artifact storage.

## 8. Shared Incident Knowledge: FCAPSule Atlas

An opt-in integration with an independent shared-case service is in development;
it is disabled by default and requires a separate Atlas deployment. The producer
uses a durable local publication outbox so Atlas work is separate from live source
monitoring and can be retried independently. Atlas case publication is designed
around versioned, idempotent revisions; bounded cross-instance retrieval must keep
source provenance and unverified hypotheses distinct from observations.

Before recommending production use, verify the service API and authentication
contract in the integrated deployment, complete case minimization/privacy review,
establish tenant boundaries and retention/deletion policy, and validate
independent service deployment, failure isolation,
two-instance operation, and evaluation of relevance, false matches, latency,
outages and source expiry. A shared candidate is not a common-cause conclusion or
causal learning. See the [FCAPSule Atlas guide](docs/ATLAS.md) for current status,
proposed operator workflow and evaluation criteria.

Autonomous remediation remains outside the core roadmap. FCAPSule may feed downstream AIOps systems, but any action must use a separate approval and safety boundary.
