# FCAPSule Roadmap

FCAPSule provides a single-replica Kubernetes control plane, live Prometheus/OpenSearch/Kubernetes adapters, domain-balanced evidence capsules, model profiles, and three operational web views. The remaining roadmap focuses on hardening, additional integrations, scale, and broader evaluation.

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
- cross-capsule similarity and recurrence detection;
- explicit evidence exclusion reasons in the UI;
- reviewer feedback and relevance labels.

## 4. AI Orchestration

Implemented: joint episode assessment, fixed read-only tool dispatch, incremental
observation retention, peer/preceding-window comparisons, omitted-log inspection,
competing hypotheses, bounded calls and provider-reported token usage. These are
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
- incident filters and search;
- capsule comparison and version history;
- authenticated sharing and portable human-readable export (JSON/ZIP downloads and local incident URLs exist);
- reviewer notes;
- configurable evidence budgets; global incident retention is already available in Settings.

## 6. Evaluation

- multiple complex synthetic scenarios;
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

Autonomous remediation remains outside the core roadmap. FCAPSule may feed downstream AIOps systems, but any action must use a separate approval and safety boundary.
