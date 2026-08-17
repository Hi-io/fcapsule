# Design Decisions

## Dependency-Light Local Service

The reference implementation uses `argparse`, the standard library HTTP server, SQLite, and PyYAML. This keeps local operation transparent and reproducible. A production HTTP framework can replace the transport without changing the control-plane or capsule contracts.

## SQLite Metadata, Source-Owned Telemetry

SQLite is sufficient for one local process and makes multi-application state durable. Raw telemetry remains in observability sources. This avoids turning FCAPSule into a second telemetry warehouse.

## Two Views, One Control Plane

Operations and Incident Lab share the same state and APIs. The lab is a source/test harness; Operations is the product surface. Keeping them together ensures simulated incidents follow the same lifecycle as future live-source incidents.

## Deterministic Core

External model availability and privacy approval cannot be assumed. Evidence selection, baseline hypotheses, and citation verification therefore work without credentials. Pretrained models are an optional reasoning layer over the same selected evidence.

## Domain-Balanced Selection

Global ranking alone allowed similarly scored PM series to displace diagnostic logs. The selector now reserves capacity per domain and prioritizes representative error, retry, latency, lock, pool, and telemetry-health groups before filling by score.

## Representative Signal Evaluation

A large system can expose many correlated derivatives of the same behavior. Signal preservation counts representative diagnostic groups instead of treating every related metric as independently essential.

## On-Demand Traces

Raw spans are high-volume and short-lived. FCAPSule verifies that they can be queried during the incident and retains only availability and derived evidence. This preserves investigative capability without violating the product's compact-retention goal.

## Cautious Causality

The simulator knows which fault was injected, but the capsule is evaluated as an investigator would see it. Hypotheses remain tentative and request database or trace evidence before calling a final root cause.

## Background Jobs and Polling

Simulation and model calls cannot block HTTP requests. The control plane runs jobs in background threads and exposes a snapshot polled by the browser. A distributed deployment should replace threads with a durable job queue.

## No Autonomous Remediation

FCAPSule produces evidence and next checks. System changes require a separate, explicitly authorized control boundary.

