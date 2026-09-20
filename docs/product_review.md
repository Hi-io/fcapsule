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

The operations console starts with an incident queue. Clicking an episode row expands
its investigation inline. A report selector separates its individual alert captures.

- **Overview**: automatically generated AI assessment, likely mechanism, first
  diagnostic check, expected finding, conditional mitigation, uncertainty, and
  material observed impact. Missing credentials and analysis failures remain explicit.
- **Evidence**: expandable alerts, log examples, performance charts with captured
  time ranges, configuration snapshots, coverage and trace-source availability.
- **Timeline**: episode alerts and an expandable captured-evidence sequence.
- **Export**: retained report JSON and capsule archive.

The AI assessment links to retained evidence. It is guidance, not a verified root
cause or an automatically executed remediation. See `ux_investigation_review.md`
for the browser audit, iterations and validation limits.

Engineering diagnostics are collapsible. They contain capsule-efficiency and validation
figures for maintainers without making them the incident response workflow.

FCAPSule Lab remains optional and separate. It creates a deterministic local failing
scenario, then exports a bounded normalized case through the same ingestion contract
used by future source adapters. It is not required to run the operations console, and a
deployment ships FCAPSule independently of the lab containers.

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

## Report Interaction Review (1.2)

An operator review of the first report view identified that a standalone report below
the queue broke the investigation flow, raw metric names were too ambiguous, and a
single generic evidence list required the reader to mentally reconstruct the source
domains. The updated interaction follows these decisions:

- A report expands directly beneath its incident queue row and can be closed in place.
- The queue calls its counts **captured context**, not impact. The application column
  identifies the registered application rather than implying a pod or container name.
- Impact cards state the measurement meaning and normal comparison. For example, p95
  explicitly explains the request-latency percentile and counter metrics state that
  they are interval observations.
- Evidence is presented through distinct FM alert records, PM trend lines, and
  expandable anonymized log patterns. CPU and memory are called out as unknown when
  the connected PM source did not provide them; the UI never pretends they were ruled
  out.
- The time-sensitive trace retrieval action is ordered first, ahead of ordinary
  investigation checks.
- The retained package is exported directly as a report JSON file or a capsule archive.

### Optional AI Briefing Gate

The report can request a short DeepSeek Pro briefing after it is already available. The
briefing receives only a compact list of retained FM, PM, and log evidence. It must cite
two to five existing evidence IDs and must state an uncertainty; otherwise FCAPSule
rejects it. A successful briefing is retained in `ai_briefing.json` and included in the
capsule archive, but prompts and provider transcripts are not archived.

In the verified checkout scenario, the model cited the retained pool-exhaustion,
deadline, and retry log patterns and prioritized failed-request trace retrieval. The
call completed in about 19.5 seconds. This validates its use as an optional, grounded
second reading while preserving the deterministic evidence report as the immediate
operator surface.
