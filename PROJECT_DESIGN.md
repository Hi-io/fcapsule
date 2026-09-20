# FCAPSule Product Design

## Design Summary

FCAPSule is a dependency-light Python control plane with a normalized evidence pipeline, SQLite metadata, CLI commands, three operator views, and live Kubernetes source adapters. It captures a bounded incident window, derives evidence and retains a capsule. Source systems remain authoritative, but live raw captures are also staged on disk until managed retention/deletion; see [storage policy](docs/data_privacy.md).

The architecture deliberately separates four responsibilities:

1. **Collection:** source adapters or an external workload export create a normalized case.
2. **Attention:** deterministic processors score and select cross-domain evidence.
3. **Reasoning:** deterministic and optional pretrained models produce grounded investigation paths.
4. **Control plane:** applications, incidents, capsules, settings, and review state are persisted and displayed.

## Implemented Components

| Component | Responsibility |
|---|---|
| `fcapsule/io/case_loader.py` | validate normalized cases |
| `fcapsule/processing/` | entity alignment, anonymization, log templates, PM anomalies, FM timeline |
| `fcapsule/attention/` | transparent scoring and domain-balanced selection |
| `fcapsule/reasoning/` | deterministic hypotheses, verification, DeepSeek comparison |
| `fcapsule/evaluation/` | baselines, reduction, preservation, grounding, retention metrics |
| `fcapsule/store.py` | SQLite application/incident/capsule/model metadata |
| `fcapsule/control_plane.py` | external-case ingestion, capsule jobs, and local AI configuration |
| `fcapsule/live_sources.py` | discovery, source health, alert polling, and bounded live capture |
| `fcapsule/adapters/` | Prometheus, OpenSearch, Kubernetes, and HTTP transport boundaries |
| `fcapsule/ui/app.py` | Operations, Targets, and general Settings web application |
| `fcapsule/cli.py` | operator and automation entry point |
| `deploy/kubernetes/` | RBAC, state volume, Deployment, Service, alert rule, and development overlay |

## Evidence Selection

Candidate evidence exposes these components:

- severity;
- anomaly magnitude;
- proximity to the alert;
- entity match;
- rarity;
- semantic relevance;
- repetition penalty.

Selection uses per-domain quotas and representative signal groups. This prevents a large family of similarly scored PM series from displacing diagnostic log patterns or FM events.

The default maximum is twenty retained evidence items:

- up to four FM events;
- up to six log templates;
- up to ten PM anomalies;
- remaining capacity filled by global score.

Within logs and PM, representatives for errors, retries, latency, pool saturation, locks, and telemetry health are considered before redundant candidates.

## Reasoning

The deterministic reasoner provides a credential-free baseline. For the reference incident it can connect retry evidence, pool evidence, related PM changes, and the FM trigger into a tentative investigation path.

The deterministic verifier rejects unknown evidence IDs, bounds confidence, and reduces confidence when required data is missing. The automatic model assessment receives compact retained evidence and must return structured JSON with valid references and uncertainty. Neither mechanism proves that cited evidence entails every generated claim.

## Storage

SQLite stores metadata, not source telemetry. The schema includes:

- `applications`;
- `incidents`;
- `incident_episodes` and `episode_incidents`;
- `capsules`;
- `model_profiles`;
- `settings`.

Live captures and generated artifacts live under the configured state directory (default `.fcapsule/`). External input directories are referenced in place. Retention defaults to 30 days from capture for active and archived incidents. A current report is read directly from its retained JSON without reopening the input case. Export sizes are measured from actual artifact files, not the capsule JSON metadata size.

## Web Application

The web application uses the Python standard library HTTP server. This keeps local setup small and makes the CLI the primary contract. The pages use a shared JSON API:

- `GET /api/state`;
- `GET /api/incidents/<id>/report`;
- `GET` and `POST /api/settings/general`;
- `GET /api/capsules/<id>`;
- `POST /api/capsules`;
- `GET /api/settings/ai`;
- `POST /api/settings/ai`.
- `GET` and `POST /api/settings/sources`;
- `POST /api/sources/test`;
- `POST /api/sources/sync`;
- `GET /healthz`.

Long-running pipeline work executes on background threads. Two workers generate optional automatic assessments after evidence is saved. The client polls every four seconds while visible and preserves report selection, disclosure state and editable target fields. Source simulation remains outside this repository.

## Trace Policy

External normalized cases may supply trace metadata describing:

- whether the source was available;
- the source retention window;
- whether access was verified;
- the number of ephemeral spans observed;
- that zero raw spans were retained.

A live trace-backend adapter is not implemented. Supplied historical metadata is not a live connectivity check. A future adapter may fetch trace-derived facts, but raw spans must not enter the archive.

## Workload Validation Boundary

The separate FCAPSule Lab project owns its workloads, databases, traffic and controlled failures. It can export normalized cases or run as Kubernetes workloads observed by the configured sources. The product neither embeds the lab nor controls its containers. Its service inventory is not part of this repository's contract.

## Extension Boundaries

Live adapters return the normalized case shape. Implemented integrations include:

- Prometheus active-alert and range queries;
- OpenSearch bounded Filebeat log queries;
- Kubernetes pod, PodSpec, and referenced ConfigMap lookups.

Extension targets include:

- Alertmanager webhooks;
- OpenTelemetry/Tempo/Jaeger on-demand trace queries;
- Kafka lag metadata.

The Kubernetes deployment includes read-only RBAC, ServiceAccount authentication, health probes, persistent state, an optional secret reference, and a development overlay. Durable SQL, object storage, job workers, application authentication, and adapter-specific retry/circuit-breaking remain scale-hardening work.

## Known Limits

- Prometheus, OpenSearch, and Kubernetes adapters are functional reference clients; they still need production authentication variants, retry policy, and large-cluster pagination/load testing.
- SQLite and in-process threads are single-node choices.
- The log parser is Drain-inspired deterministic masking rather than semantic clustering.
- PM analysis uses robust explainable statistics rather than a pretrained forecasting model.
- The reference scenario is synthetic and cannot establish production root-cause accuracy.
- Model quality scores evaluate grounded use of known evidence, not whether a model discovered definitive causality.
