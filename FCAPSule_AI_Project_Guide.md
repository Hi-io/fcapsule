# FCAPSule Project Guide

**Status:** Current implementation and product requirements, September 2026.

**Audience:** operators, maintainers and evaluators.

**Concept:** [Stable purpose and research framing](FCAPSule_AI_Concept.md).

**Design:** [Implemented architecture](PROJECT_DESIGN.md).

## Purpose

Preserve useful incident context before source telemetry expires, and help an SRE or application engineer decide what to investigate next. A compact capsule should explain what was observed, why it matters, what could explain it and what remains uncertain.

FCAPSule complements observability infrastructure. It is not a replacement telemetry warehouse, a guaranteed root-cause detector, an autonomous remediation agent or a model-comparison dashboard.

## User Workflow

1. Configure Prometheus, OpenSearch and Kubernetes access in Targets.
2. Check current applications, grouped by namespace, and observed telemetry coverage.
3. A firing alert opens a bounded capture; pending alerts do not open incidents.
4. Related signals for the same application within the correlation window join an episode.
5. The deterministic pipeline retains evidence and builds an immediately usable report.
6. With a provider key, the selected model produces an automatic background assessment.
7. Open the episode in Operations, inspect Overview, then follow citations into Evidence.
8. Use Timeline for ordering, Export for handoff, and archive to clear the working queue.
9. Retention removes old incidents and their managed files, including archived incidents.

The CLI remains usable independently. External normalized cases may be ingested manually. Simulation belongs to a separate project; the product neither deploys workloads nor injects failures.

## Operational Domains

| Domain | Implemented input | Operator value |
|---|---|---|
| Fault management | Prometheus firing alerts, optional matching rule expression and duration | Why the alert fired, its scope and sequence |
| Performance | Prometheus numeric series | Baseline/incident changes and retained trends |
| Logs | OpenSearch bounded log documents | Counted patterns, masked examples and timing |
| Configuration | Kubernetes pod context and referenced ConfigMaps | Runtime limits, images and configuration associated with the affected workload |
| Trace context | Optional metadata in imported cases | Declared availability and retention; no live trace backend yet |

These are operational data domains, not audio/image/text media modalities. A source connection, recently observed data and selected incident evidence are different states. An empty domain must remain explicit rather than be filled with invented values.

## Screens and Priorities

### Operations

The first screen answers: what needs attention, which application is affected, what is the observed impact, and where is its investigation?

An expandable episode owns its report selector. Overview shows the AI assessment when available, material impact, a likely explanation, a first check, expected finding, conditional mitigation and uncertainty. Provider errors or a missing key must not block retained evidence. Evidence and Timeline provide progressive detail. Engineering metrics stay collapsed and model comparisons remain offline.

Export makes artifact size, retention eligibility and server-side location inspectable. A current retained report must remain readable after its source capture disappears.

### Targets

Connection health, discovery counts and namespace-grouped coverage are the primary information. Source configuration is expandable. Background refresh and connection tests must not erase unsaved input. Tests use saved connection settings; save edited values before testing them.

### Settings

Incident retention and automatic AI briefing configuration share one page. Retention defaults to 30 days from capture and includes archived incidents. Model choice, completion budget and replacement key are editable. No additional screen is required for the current workflow.

## Evidence and Reasoning Contract

The deterministic path validates inputs, aligns entities, reduces logs, identifies PM changes, constructs the alert timeline, scores candidates and selects domain-balanced evidence. It works without external credentials.

The optional runtime model sees compact retained evidence. Its structured response must cite existing evidence IDs and state uncertainty. Reference validity does not establish causal truth, diagnosis quality or complete factual grounding. Human review remains necessary; no changes are executed against monitored systems.

Offline comparison can run multiple compatible models over identical input. Fixed rubrics, latency and token usage support evaluation, not a predetermined winner. Ties and worse results are valid outcomes.

## Storage and Privacy Contract

SQLite holds metadata; managed files live under the configured state directory. Live capture currently stages bounded raw inputs in `live-cases/` until incident deletion/retention. The capsule ZIP excludes those inputs, retaining derived evidence, representative lines and chart values. External cases outside managed state remain producer-owned.

Masking is heuristic and cannot guarantee removal of every sensitive value. Configuration collection does not read Kubernetes Secrets. A UI-saved provider key lives in plaintext in the ignored state-directory `.env`, not SQLite or API responses. See [privacy and retention](docs/data_privacy.md) for exact boundaries.

Archiving is not deletion and does not reset retention. Exported copies are independent of the service's cleanup.

## Deployment and Maturity

The implemented deployment is a single Kubernetes replica with read-only ServiceAccount access, PVC-backed state, health probes and an optional model credential. Live Prometheus/OpenSearch/Kubernetes adapters exist today.

The reference HTTP server has no built-in authentication, authorization or TLS. SQLite and in-process workers are not a distributed service. Trusted-network operation is supported; production hardening, durable queues, source scaling and live trace integration remain [roadmap work](ROADMAP.md). A polished UI must not be used as evidence that those controls exist.

## Acceptance Criteria

- An engineer can identify the affected workload, observed symptom and next check without opening engineering diagnostics.
- Related alerts are grouped without losing individual reports or provenance.
- Missing evidence, source failures and incomplete AI analysis remain visible.
- Report and archive retrieval work independently of expired source telemetry.
- Targets show currently observed workloads, not deleted pods as active coverage.
- No automatic comparison or simulator is required in the operator path.
- Archive, restore, deletion and retention have distinct behavior.
- Tests cover pipeline, lifecycle, report persistence, HTTP routes and UI helpers.
- Desktop and narrow layouts preserve usable controls, focus and readable evidence.
- Documentation separates implemented behavior, historical measurements and remaining limitations.
