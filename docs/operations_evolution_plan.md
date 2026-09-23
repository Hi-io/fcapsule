# Operations Evolution Plan

This is a historical design record. Its proposed triage strip was later removed
from the operator interface after usability review; the queue now leads
Operations, and potential shared conditions live in Patterns. See
[Operations Guide](operations.md) for current behavior.

## Goal

Make Operations answer two operational questions without turning it into a
telemetry dashboard:

1. What needs attention now?
2. Has this happened before, and what is different this time?

Each captured episode remains independently auditable. Correlation, recurrence
and AI-generated comparisons are decision support; they never merge historical
incidents, replace original evidence, or claim a shared cause without evidence.

## Work Items

### 1. Stable incident identity and direct sharing

- Show a short, stable incident reference derived from the immutable incident
  identifier alongside each expanded episode.
- Preserve the existing URL-based direct opening behaviour and add a copy-link
  control at the point where an operator reads the investigation.
- Keep the full internal identifier available in export data and engineering
  diagnostics only.

Acceptance criteria:

- A responder can copy a link to the selected incident or episode.
- Opening that link restores the episode and its selected alert report.
- The queue stays visually compact.

### 2. Resource-aware identity

- Distinguish the monitored target from the telemetry collector. A node alert
  should show the affected node, not merely the node-exporter pod that exposed
  the metric.
- Derive a compact resource label from retained alert labels and report
  evidence, with application identity as a conservative fallback.
- Keep the source collector visible in evidence, not as the primary target.

Acceptance criteria:

- Node, workload and application incidents can be identified from the queue.
- No inferred resource identity overwrites captured source data.

### 3. Deterministic recurrence history

- Store an episode pattern key based on the affected application/resource and
  normalized alert identity.
- Compute recurrence count, first/last occurrence and observed interval from
  retained episodes using the same key.
- Expose a concise recurrence badge only when an earlier episode exists.
- Keep unrelated incidents separate even when they occur on a schedule.

Acceptance criteria:

- A repeated alert reports how many earlier episodes match it in the retained
  history.
- The interval is described as observed history, not a prediction.
- Archived episodes remain part of recurrence history until retention removes
  them.

### 4. Evidence-based historical comparison

- Offer a bounded historical-episode tool to the existing AI investigator only
  when deterministic recurrence candidates exist.
- Require the model to identify supporting differences or similarities from
  preserved evidence, and label the conclusion as a comparison rather than a
  root-cause verdict.
- Preserve the query and references in the investigation record so the result
  is reviewable after source telemetry expires.

Acceptance criteria:

- The model cannot inspect arbitrary historical data or execute actions.
- Historical conclusions cite retained observations.
- An earlier assessment is treated as a hypothesis, not a fact.

### 5. Focused Operations triage

- Add a small "Needs attention" strip above the queue.
- Surface only actionable items: active incidents, recurring patterns and
  material changes from the closest comparable earlier episode.
- Deduplicate signals so several alerts from one episode produce one item.
- Omit the strip when there is no supported action worth showing.

Acceptance criteria:

- The summary contains at most three items.
- Every item links to the relevant episode.
- It does not repeat raw metric values, model token usage or generic summaries.

### 6. Queue filters and pattern exploration

- Add compact filters for namespace, resource/application, lifecycle and time
  range, plus ID/text search.
- Persist filters in the URL for collaboration and browser navigation.
- Add a Patterns view for recurring issues, ordered by recent activity and
  recurrence count. It must link back to individual episodes rather than
  becoming a separate incident record.
- Reserve a future Capsules library for retained evidence search and export;
  do not introduce it until it has a distinct workflow.

Acceptance criteria:

- Filters work without hiding incidents outside the selected scope from stored
  history.
- Pattern rows state the affected resource, count and observed recurrence
  window, then open the episode timeline.
- Operations remains the current-work queue; Patterns is historical context.

## Delivery Order

1. [x] Data schema, migration and deterministic recurrence calculations.
2. [x] API snapshot fields, resource identity and direct incident links.
3. [x] Queue filters, recurrence badges and the focused triage strip.
4. [x] Pattern explorer and historical-investigation tool.
5. [x] UI review, automated tests, live source verification and documentation.

## Non-Goals

- Automatic incident merging or remediation.
- A generic AI chat surface.
- Forecasting recurrence from a small history.
- Retaining raw source telemetry beyond the configured FCAPSule policy.
