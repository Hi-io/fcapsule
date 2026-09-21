# Design Decisions

## Dependency-Light Local Service

The reference implementation uses `argparse`, the standard library HTTP server, SQLite, and PyYAML. This keeps local operation transparent and reproducible. A production HTTP framework can replace the transport without changing the control-plane or capsule contracts.

## SQLite Metadata, Source-Owned Telemetry

SQLite is sufficient for one process and makes multi-application metadata durable. Observability sources remain authoritative. Bounded live inputs are currently staged on the state volume until incident cleanup; derived-only ZIP exports must not be confused with a raw-free volume. A shorter staging TTL is future work.

## Three Views, One Control Plane

Operations is the product surface. The source/test harness is now the separate FCAPSule
Lab project, which exports the same normalized incident contract used by live-source
adapters. Targets configures production data sources and shows current workload coverage; Settings combines retention policy with optional cited reasoning. Keeping the contract shared while keeping runtime ownership
separate prevents the product from becoming a simulator or container controller.

## Deterministic Core

External model availability and privacy approval cannot be assumed. Evidence selection, baseline hypotheses, and citation verification therefore work without credentials. Pretrained models are an optional reasoning layer over the same selected evidence.

## Episodes Above Alert Signals

Prometheus alerts are retained as individual signals, but Operations groups firing signals for the same application inside a 15-minute activity window. This makes one service degradation one operator task while preserving every source alert and report. Pending rules do not open episodes. Correlation and lifecycle state are deterministic so alert grouping remains explainable when the model provider is unavailable.

## Domain-Balanced Selection

Global ranking alone allowed similarly scored PM series to displace diagnostic logs. The selector now reserves capacity per domain and prioritizes representative error, retry, latency, lock, pool, and telemetry-health groups before filling by score.

## Representative Signal Evaluation

A large system can expose many correlated derivatives of the same behavior. Signal preservation counts representative diagnostic groups instead of treating every related metric as independently essential.

## On-Demand Traces

Raw spans are high-volume and short-lived. The contract accepts externally supplied availability and retention metadata without retaining spans. Live trace verification/query is a future adapter, not part of the deployed Kubernetes path.

## Cautious Causality

The external workload may know which fault it injected, but the capsule is evaluated as
an investigator would see it. Hypotheses remain tentative and request database or trace
evidence before calling a final root cause.

## Background Jobs and Polling

Capture and model calls must not block HTTP requests. The control plane uses background threads and a polled snapshot. A distributed deployment should replace threads with a durable job queue. Simulation is outside the product.

## Durable Report Reading

A current report is served from retained JSON rather than reopening the original case on every view. This keeps incident review usable after source expiration. A legacy rebuild falls back to retained capsule evidence when input files are missing; it does not invent replacement raw samples.

## No Autonomous Remediation

FCAPSule produces evidence and next checks. System changes require a separate, explicitly authorized control boundary.

## Evidence Specialists Are Opt-In

The core investigator works on retained text and structured operational evidence. A
vision specialist and an audio transcription specialist can enrich an already
captured episode only when an operator supplies a file and each required capability
has been validated. This keeps screenshots and spoken observations out of automatic
collection, preserves an explicit provenance boundary, and avoids spending specialist
model calls on routine incidents.

## Numerical Specialist Deferred Until It Demonstrates Incremental Value

Performance-management data is currently summarized by deterministic range queries,
baseline comparison, units-aware presentation, and bounded trend samples before the
core investigator sees it. A separate pretrained numerical/time-series model is not
enabled merely to add another model. It will be introduced only after a controlled
evaluation shows that it improves useful discrimination over these summaries for the
same incidents, without unacceptable latency or operational cost. This preserves a
clear product boundary while leaving room for an evidence-backed specialist later.
