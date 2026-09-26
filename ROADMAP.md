# FCAPSule Roadmap

FCAPSule provides a single-replica Kubernetes control plane, live Prometheus/OpenSearch/Kubernetes adapters, domain-balanced evidence capsules, model profiles, and five operational web views, including the optional Collective client and memory explorer. The remaining roadmap focuses on hardening, additional integrations, scale, and broader evaluation.

Implemented: bounded live-source staging expires independently of incident capsules
after a configurable TTL (24 hours by default). Cleanup is snapshot-driven; explicit
rebuild guarantees after staged-source expiry remain future work.

## 1. Source Hardening and Expansion

- Alertmanager webhook receiver and alert query support;
- Tempo, Jaeger, or OpenTelemetry trace availability and on-demand query;
- Kafka/OpenSearch indexing-lag metadata;
- bearer token, TLS client certificate, and external Secret references;
- retries, backoff, circuit breaking, pagination, and source rate limits;
- multi-cluster target registry and per-source query diagnostics.
- explicit guarantees for rebuilding after staged-source expiry;

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
- per-user identities, RBAC and SSO for FCAPSule operations (the Kubernetes console requires Basic Auth, but that does not distinguish user roles; standalone development without console credentials is loopback-only);
- portable human-readable export (JSON/ZIP downloads and local incident URLs exist);
- reviewer notes;
- per-domain evidence inclusion budgets (investigation token, input and additional-check limits are already configurable in Settings); global incident retention is already available.

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

## 8. Shared Incident Knowledge: Collective

The optional FCAPSule client and memory explorer are implemented and disabled by
default. Each instance investigates locally, runs configured model calls, prepares
a minimized case, and can publish it through a local retryable outbox or retrieve
versioned cases from the independent Collective service. Collective has its own
repository, API and PostgreSQL database; it does not run models, need provider keys
or generate token spend. Interpretation of retrieved cases happens in the
requesting FCAPSule instance. See the [Collective integration guide](docs/collective.md)
for the current contract and limits.

The API carries provenance and keeps observations distinct from unverified
hypotheses. Candidate similarity and repeated observations are retrieval cues,
not a learned causal model, a verified root cause or an adjudicated outcome.

Production use still requires case minimization/privacy review, access boundaries,
retention/deletion policy, relevance, false matches, latency, outages and source
expiry. The service supports instance-bound publishers, read-only readers, explicit
episode withdrawal and optional retention expiry, but has no tenant isolation;
authenticated readers can inspect cases across the deployment. FCAPSule local
deletion does not automatically withdraw its remote copy. Local investigation must
remain useful when Collective is unavailable.

Autonomous remediation remains outside the core roadmap. FCAPSule may feed downstream AIOps systems, but any action must use a separate approval and safety boundary.
