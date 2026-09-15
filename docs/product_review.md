# Product Review: Operator-First FCAPSule

## Decision

FCAPSule is an incident-evidence product, not an AI benchmark dashboard. Its primary
interface must help an on-call engineer understand an active or recent failure while
the relevant telemetry is still available. Model comparisons, compression ratios, and
internal quality scores remain useful for engineering evaluation, but they are not the
main decision surface for an operator.

This review is the product gate used for the current implementation and for future UI
changes.

## The Operator Problem

An application engineer commonly discovers an incident through an alert, then has a
short retention window in which to assemble logs, fault-management events, performance
metrics, topology context, and traces. The difficult part is not reading a generic
summary. It is deciding, quickly and with defensible evidence:

- Which application and service are affected?
- How large is the impact and when did it begin?
- What failure path best explains the observed symptoms?
- What evidence supports that path, and what remains uncertain?
- What should be checked or preserved before the telemetry expires?

The product should retain a compact, inspectable evidence package at incident time so
that the investigation can continue after raw telemetry has aged out.

## Product Review Rubric

Every main-screen element should answer at least one operator question above or enable
an immediate action. The following information is deliberately secondary:

- Capsule size, log-reduction percentage, and signal-preservation scores.
- Prompt contents, model latency, provider configuration, and model-versus-model
  comparison results.
- Raw counts without a service, time window, severity, or operational interpretation.

Those details belong in the collapsed engineering diagnostics section, offline
evaluation artifacts, or repository documentation. They are retained for validation;
they are not removed from the system's audit trail.

## Role Checks

### On-call SRE

Needs an incident queue, severity, a concise observed impact, the likely failure path,
time-bounded evidence, and a next action. The report must distinguish an observation
from an inference and expose uncertainty rather than presenting a generated diagnosis
as fact.

### Application Engineer

Needs to identify the affected service and downstream dependency, see what changed in
the incident window, inspect representative evidence, and retrieve failed-request
traces while their source buffer is still available.

### Platform Owner

Needs coverage and retention visibility: which domains were captured, whether traces
are retained or only available at source, the archive identifier, and a stable artifact
that can be handed to another team without storing all raw traces indefinitely.

## Implemented Product Shape

The operations console now starts with an incident queue. Selecting **Open report**
opens a retained incident workspace with these sections:

- **Impact**: error rate, affected requests, latency, retries, and saturation signals
  where observed.
- **Likely failure path**: an evidence-backed hypothesis, confidence, evidence, and
  explicit uncertainty.
- **What changed**: a chronological event timeline and affected service relationships.
- **Supporting evidence**: concise fault-management, log, performance, and topology
  records with their timestamps and sources.
- **Next actions**: practical investigation and preservation actions, with trace
  retrieval called out as urgent when applicable.
- **Coverage**: captured domains, retained evidence items, trace-source availability,
  and archive location.

Engineering diagnostics are collapsible. They contain capsule-efficiency and validation
figures for maintainers without making them the incident response workflow.

The Incident Lab remains optional. It creates a deterministic local failing scenario
and shows capture progress so the product can be demonstrated and tested without an
external application. It is not required to run the operations console.

## Validation Evidence

The current verification scenario used the `orders-checkout` service with concurrent
checkout requests, inventory-pool contention, retry amplification, slow responses, and
downstream failures. It produced 1,694 structured logs, 21 metric series, and three
alerts. The captured incident reported a 51.7% error rate, 20 checkout errors, 142 ms
p95 latency versus a 22 ms baseline, a 2.06x retry factor, and inventory-pool pressure.

FCAPSule preserved the evidence capsule, generated the incident report, identified the
retry-amplification path as plausible, listed the missing proof needed to confirm it,
and retained a 900-second failed-request trace retrieval window. The report was built
without waiting on a model provider.

An earlier live provider experiment demonstrated why this boundary matters: one
provider response took roughly 89 seconds and returned incomplete structured output.
The production report remains deterministic and available even in that condition.
Provider/model experiments are therefore offline evaluation work, not a dependency of
incident capture or report generation.

## Remaining Production Work

The repository implements the product contract and a local demonstration source. A
distributed deployment should add:

- Collectors/adapters for real log, metrics, alert, topology, and trace systems.
- Source-system deep links and authenticated trace retrieval.
- Incident acknowledgement, ownership, escalation, and notification integrations.
- Multi-tenant access control, audit controls, encryption, and retention policy
  configuration.
- Durable background workers, back-pressure limits, and health monitoring for the
  collectors and archive store.

These are deployment and integration concerns. They do not change the operator-first
reporting contract established by this review.
