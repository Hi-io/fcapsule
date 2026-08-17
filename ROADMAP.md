# FCAPSule Roadmap

FCAPSule 1.0 provides a complete local control plane, a realistic regression lab, domain-balanced evidence capsules, model profiles, and two operational web views. The remaining roadmap focuses on live integrations, deployment, scale, and broader evaluation.

## 1. Live Source Adapters

- Alertmanager webhook receiver and alert query support;
- Prometheus range-query client with query templates;
- OpenSearch bounded log search with source-field mapping;
- Kubernetes workload/topology resolver;
- Tempo, Jaeger, or OpenTelemetry trace availability and on-demand query;
- Kafka/OpenSearch indexing-lag metadata;
- credential references, timeouts, retries, and per-source health.

## 2. Service Deployment

- FastAPI or equivalent production HTTP boundary;
- background job queue;
- PostgreSQL metadata;
- object storage for immutable capsule artifacts;
- Kubernetes manifests or Helm chart;
- readiness, liveness, and metrics endpoints;
- role-based access and audit logging;
- retention policies and integrity hashes.

## 3. Evidence Quality

- calibrated scoring weights from reviewed cases;
- semantic log embeddings as an optional model;
- stronger contradiction handling;
- configuration/deployment change correlation;
- cross-capsule similarity and recurrence detection;
- explicit evidence exclusion reasons in the UI;
- reviewer feedback and relevance labels.

## 4. AI Orchestration

- provider-neutral model registry;
- additional pretrained LLM comparisons;
- pretrained time-series methods compared with the statistical baseline;
- semantic log model comparison;
- cost budgets and rate limiting;
- prompt and rubric versioning;
- model result history and reviewer preference.

## 5. Operations Experience

- application registration editor;
- live adapter status and query diagnostics;
- incident filters and search;
- capsule comparison and version history;
- downloadable reports and shareable links;
- reviewer notes;
- configurable evidence limits and retention.

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

