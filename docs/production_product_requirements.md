# FCAPSule Production Product Requirements

## Purpose

FCAPSule protects the investigation context around an operational incident before source telemetry expires. It is not a generic log summarizer and it is not a model-evaluation dashboard. Its job is to turn a fault-triggered, short-lived observation window into a compact, traceable incident record that an SRE or application engineer can act on.

The product must remain useful when raw logs, metrics, or traces are retained by their source systems for only a short period. A capsule keeps derived, attributable evidence and investigation context; it does not become a second long-term raw telemetry store.

## Primary Users and Jobs

### On-call SRE

When an alert fires, the SRE needs to answer four questions quickly:

1. What is failing, how severe is it, and which service or dependency is affected?
2. What changed in the incident window, in what order, and what evidence supports that sequence?
3. What should be checked or mitigated next, before evidence expires?
4. What evidence remains available, and where should the investigation continue?

### Application Engineer

The engineer needs a report that connects symptoms in their service with dependency behavior, configuration context, and representative logs. They need hypotheses with explicit evidence and uncertainty, not an unsupported claim that an AI found the root cause.

### Platform Owner

The owner needs to see which applications are protected, whether their FM, PM, log, and trace sources are connected, and whether an incident has a retained report ready for handoff.

## Product Principles

- **Incident first.** The default view is an incident queue and application coverage, not pipeline statistics.
- **Action before explanation.** Impact, likely failure path, urgent checks, and expiring evidence appear before supporting detail.
- **Evidence over prose.** Every hypothesis must link back to selected FM, PM, log, topology, or trace-access evidence. Uncertainty stays visible.
- **Retention is operational context.** A trace source available for the next few minutes is a time-sensitive action, not a storage implementation detail.
- **No model contest in the operator workflow.** Model selection is an administrator and evaluation concern. The runtime uses the configured model, while users see grounded findings and evidence rather than scores, latency, or token counts.
- **Technical evaluation stays available, but out of the way.** Compression, signal-preservation, grounding, and model-evaluation details may be exposed in an expandable engineering diagnostics section for maintainers and research reports.
- **Do not duplicate observability storage.** FCAPSule stores the compact capsule, source references, and report; raw telemetry stays with the source system.

## Required Operator Experience

### Incident workspace

The main console must show:

- open and recently captured incidents ordered by severity and time;
- service, environment, start time, affected scope, and report readiness;
- a direct **Open report** action for every incident;
- application coverage: which operational domains are connected for each application.

The workspace must not make log reduction, retained bytes, model scores, token use, or a model comparison its primary content.

### Incident report

Opening a report must provide a focused investigation surface with these sections in order:

1. **Incident status and impact:** severity, service, environment, start time, user-facing symptom, and material impact metrics.
2. **Likely failure path:** evidence-grounded, probabilistic hypothesis with explicit confidence and uncertainty.
3. **What changed:** ordered FM alerts, key PM anomalies, and relevant dependency/topology context.
4. **Do next:** concrete checks and mitigations. Trace or source-data actions must be marked urgent when their source retention window is short.
5. **Evidence available:** domain coverage, source references, and the boundary between retained derived evidence and source-only raw telemetry.
6. **Supporting evidence:** representative logs, metric anomalies, and evidence identifiers for auditability.
7. **Engineering diagnostics:** collapsed by default; contains compression, preservation, grounding, runtime, and optional model-evaluation metadata.

## Data Contract

The current local implementation already supplies the required inputs:

- FM: alert name, severity, timestamp, labels, and annotations;
- PM: anomaly baseline, incident peak, percentage change, timestamp, component, and metric name;
- logs: de-duplicated representative templates and linked entities;
- topology/configuration: service/dependency chain and injected or observed configuration context;
- traces: availability, source retention horizon, and a statement that raw spans are not retained;
- reasoning: deterministic hypotheses, supporting evidence IDs, missing evidence, next checks, and verification notes.

For a deployed integration, source adapters must additionally provide a stable source query or deep link for each retained evidence item where the source system supports one. This is a deployment requirement, not an invitation to persist raw data in FCAPSule.

## Runtime and Deployment Boundaries

FCAPSule should run as a pod or service alongside existing observability systems. It receives incident triggers, resolves the configured application identity, gathers a bounded investigation window from FM, PM, logs, topology/configuration, and optional trace access, then writes a capsule and report to durable storage.

FCAPSule Lab remains an optional, separate Compose repository. It validates adapters and
produces reproducible sample data, but is not part of the FCAPSule operator workflow or
deployment artifact.

## Acceptance Criteria

An operator should be able to open an incident report and, without reading engineering diagnostics, identify the affected service, observed impact, plausible failure path, evidence timing, immediate next checks, and urgent retention-sensitive actions.

The report must distinguish observed facts from hypotheses. It must not state a final root cause unless that conclusion is independently supported by retained evidence.

An internal product review must reject a screen or field when it does not help an operator decide what happened, what is affected, what to do next, or what evidence is about to disappear.

## Iteration Checklist

For every UI or pipeline change, review it from the three user roles above:

1. Does it shorten time to a safe next action?
2. Does it make evidence, scope, uncertainty, and retention clearer?
3. Is it presenting a development metric as if it were an operational outcome?
4. Can the information be derived reliably from the configured sources?
5. Does it keep raw telemetry ownership with the source system?

If the answer to the first two questions is no, the element should be removed, moved to engineering diagnostics, or redesigned.
