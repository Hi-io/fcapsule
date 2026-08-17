# FCAPSule Product Design

## Design Summary

FCAPSule is implemented as a dependency-light Python control plane with a normalized evidence pipeline, local SQLite metadata, CLI commands, and two web views. Raw telemetry is not treated as product storage. The system queries a bounded window, derives evidence, retains the capsule, and leaves raw data in the source platform.

The architecture deliberately separates four responsibilities:

1. **Collection:** source adapters or the incident lab create a normalized case.
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
| `fcapsule/control_plane.py` | asynchronous simulation and capsule jobs |
| `fcapsule/ui/app.py` | Operations and Incident Lab web application |
| `demo/incident_lab.py` | real local checkout/inventory failure scenario |
| `fcapsule/cli.py` | operator and automation entry point |

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

The verifier rejects unknown evidence IDs, bounds confidence, and reduces confidence when required data is missing. Optional external models receive only compact evidence and must return structured JSON.

## Storage

SQLite stores metadata, not source telemetry. The schema includes:

- `applications`;
- `incidents`;
- `capsules`;
- `model_profiles`;
- `settings`.

Case exports and generated capsule artifacts live under the configured state directory. In local mode the default is `.fcapsule/`.

## Web Application

The web application uses the Python standard library HTTP server. This keeps local setup small and makes the CLI the primary contract. The pages use a shared JSON API:

- `GET /api/state`;
- `GET /api/capsules/<id>`;
- `POST /api/simulations`;
- `POST /api/capsules`;
- `POST /api/models/<id>`;
- `POST /api/reset`.

Long-running simulation, pipeline, and model work executes on background threads. The client polls current state and renders phase progress.

## Trace Policy

The lab exposes an ephemeral trace probe. FCAPSule records:

- whether the source was available;
- the source retention window;
- whether access was verified;
- the number of ephemeral spans observed;
- that zero raw spans were retained.

A production adapter may fetch trace-derived facts during the incident window, but raw span payloads must not enter the capsule archive.

## Failure Scenario

The simulator runs two independent HTTP servers.

1. Healthy checkout requests reserve inventory quickly.
2. A configuration reload introduces partition lock contention in inventory.
3. Affected reservations exceed the checkout client deadline.
4. Checkout retries up to three times while the circuit breaker remains closed.
5. Concurrent retries amplify inventory calls.
6. The eight-slot database pool saturates.
7. Inventory acquisition errors and checkout failures grow.
8. Retry amplification, pool saturation, and error-budget alerts fire.
9. FCAPSule captures logs, PM series, FM events, topology, configuration context, and trace availability.

The scenario is deterministic in structure but keeps real scheduling, HTTP deadlines, and concurrency behavior.

## Extension Boundaries

Live adapters must return the normalized case shape. Intended implementations include:

- Alertmanager webhook and alert queries;
- Prometheus range queries;
- OpenSearch bounded log queries;
- Kubernetes topology/configuration lookups;
- OpenTelemetry/Tempo/Jaeger on-demand trace queries;
- Kafka lag metadata.

A Kubernetes deployment should add authentication, durable SQL, job workers, health probes, and adapter-specific retry/circuit-breaking without changing the evidence pipeline contract.

## Known Limits

- The shipped live-source adapters remain export-oriented reference boundaries, not production clients.
- SQLite and in-process threads are single-node choices.
- The log parser is Drain-inspired deterministic masking rather than semantic clustering.
- PM analysis uses robust explainable statistics rather than a pretrained forecasting model.
- The reference scenario is synthetic and cannot establish production root-cause accuracy.
- Model quality scores evaluate grounded use of known evidence, not whether a model discovered definitive causality.

