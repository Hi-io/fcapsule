# Design Decisions

## Dependency-Light Local Service

The reference implementation uses `argparse`, the standard library HTTP server, SQLite, and PyYAML. This keeps local operation transparent and reproducible. A production HTTP framework can replace the transport without changing the control-plane or capsule contracts.

## SQLite Metadata, Source-Owned Telemetry

SQLite is sufficient for one local process and makes multi-application state durable. Raw telemetry remains in observability sources. This avoids turning FCAPSule into a second telemetry warehouse.

## Three Views, One Control Plane

Operations is the product surface. The source/test harness is now the separate FCAPSule
Lab project, which exports the same normalized incident contract used by live-source
adapters. Targets configures production data sources and shows current workload coverage; Settings combines retention policy with optional cited reasoning. Keeping the contract shared while keeping runtime ownership
separate prevents the product from becoming a simulator or container controller.

## Deterministic Core

External model availability and privacy approval cannot be assumed. Evidence selection, baseline hypotheses, and citation verification therefore work without credentials. Pretrained models are an optional reasoning layer over the same selected evidence.

## Domain-Balanced Selection

Global ranking alone allowed similarly scored PM series to displace diagnostic logs. The selector now reserves capacity per domain and prioritizes representative error, retry, latency, lock, pool, and telemetry-health groups before filling by score.

## Representative Signal Evaluation

A large system can expose many correlated derivatives of the same behavior. Signal preservation counts representative diagnostic groups instead of treating every related metric as independently essential.

## On-Demand Traces

Raw spans are high-volume and short-lived. FCAPSule verifies that they can be queried during the incident and retains only availability and derived evidence. This preserves investigative capability without violating the product's compact-retention goal.

## Cautious Causality

The external workload may know which fault it injected, but the capsule is evaluated as
an investigator would see it. Hypotheses remain tentative and request database or trace
evidence before calling a final root cause.

## Background Jobs and Polling

Simulation and model calls cannot block HTTP requests. The control plane runs jobs in background threads and exposes a snapshot polled by the browser. A distributed deployment should replace threads with a durable job queue.

## No Autonomous Remediation

FCAPSule produces evidence and next checks. System changes require a separate, explicitly authorized control boundary.
