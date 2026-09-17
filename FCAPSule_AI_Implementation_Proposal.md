# FCAPSule Implementation Proposal

**Status:** Implemented baseline for version 1.0.  
**Stable concept:** `FCAPSule_AI_Concept.md`  
**Implemented design:** `PROJECT_DESIGN.md`

## Objective

Implement FCAPSule as a source-neutral telemetry attention service with:

- application registration;
- bounded incident collection;
- FM, PM, log, topology, and trace-access normalization;
- compact evidence selection;
- grounded deterministic and optional pretrained-model reasoning;
- durable capsule metadata;
- CLI, API, Operations, and AI settings surfaces.

## Implemented Local Stack

- Python 3.11+;
- PyYAML for case metadata;
- SQLite for control-plane metadata;
- standard library HTTP server for local UI/API;
- background threads for local jobs;
- filesystem artifacts under `.fcapsule/`;
- optional DeepSeek API client.

This stack is intentionally small. The component boundaries support migration to a production HTTP framework, PostgreSQL, object storage, and queue-backed workers.

## Main Commands

```bash
python3 -m fcapsule.cli serve
python3 -m fcapsule.cli ingest-case --case <case> --app-id <id>
python3 -m fcapsule.cli investigate --case <case> --out <output>
python3 -m fcapsule.cli compare-llms --capsule <capsule.json> --out <output>
python3 -m fcapsule.cli register --app-id <id> --name <name> --namespace <ns> --cluster <cluster>
python3 -m fcapsule.cli status
```

## Source Adapter Contract

Every source adapter normalizes its bounded response into:

- `metadata.yaml`;
- `alert.json`;
- `prometheus_metrics.json`;
- `opensearch_logs.json`;
- optional expected regression notes.

Live adapters should query source APIs directly and may use an in-memory normalized object in a later implementation. File serialization remains valuable for reproducible evaluation.

## Attention Pipeline

1. Validate the case.
2. Resolve entity identity.
3. Reduce logs into templates.
4. analyze PM changes against a baseline.
5. Build the FM timeline.
6. Score every evidence candidate.
7. Select domain-balanced evidence.
8. Generate and verify hypotheses.
9. calculate objective metrics.
10. Write derived artifacts and archive.

## Control Plane

The control plane stores applications, incidents, capsules, and non-secret model preferences. It provides external-case ingestion and background capsule jobs to the web API.

Operations and AI settings are the product views. The FCAPSule Lab workload is a separate Compose project that exports the normalized case contract, so it exercises the same lifecycle without becoming a product dependency.

## Trace Approach

A trace adapter should support:

- availability probe;
- source retention discovery;
- bounded request query;
- optional derived facts;
- explicit confirmation that raw spans are not retained.

Trace evidence may inform a capsule during active investigation, but complete spans remain in the source backend.

## Production Evolution

Replace local implementation details without changing the normalized contracts:

| Local | Production direction |
|---|---|
| standard library HTTP | FastAPI or equivalent |
| background thread | durable job queue |
| SQLite | PostgreSQL |
| local artifact directory | object storage |
| file adapters | authenticated source API clients |
| local configuration | mounted config and secret references |
| single process | Kubernetes deployment |

## Engineering Priorities

1. Preserve evidence provenance and cautious causal language.
2. Keep raw telemetry source-owned.
3. Make source availability and selection state visually distinct.
4. Keep deterministic behavior available without external credentials.
5. Evaluate model changes on identical evidence.
6. Scale through adapters and workers rather than coupling the pipeline to infrastructure.
