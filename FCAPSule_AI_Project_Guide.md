# FCAPSule Project Guide

**Status:** Version 1.0 product requirements  
**Audience:** maintainers, contributors, evaluators, and operators  
**Source of truth:** stable product behavior and engineering boundaries

## 1. Purpose

FCAPSule reduces a bounded cloud incident window into a compact, explainable evidence capsule. It is designed for teams that already have observability systems but need a faster and more durable way to identify which evidence matters.

The stable product goal is:

> Preserve the strongest cross-domain investigation evidence while reducing the volume, token cost, and retention burden of raw telemetry.

FCAPSule does not need to know the final root cause to be useful. It must show what happened, what changed, which entities are involved, why evidence was selected, what remains uncertain, and which checks should happen next.

## 2. Product Boundaries

FCAPSule is:

- a telemetry attention engine;
- an alert-centered evidence collector;
- a compact incident flight recorder;
- a source-neutral normalization pipeline;
- an explainable evidence selector;
- an optional orchestration point for pretrained AI models;
- a retained evidence store and review surface.

FCAPSule is not:

- a replacement for Prometheus, OpenSearch, Alertmanager, Kafka, or tracing systems;
- a raw telemetry warehouse;
- an autonomous remediation system;
- a guarantee of final root cause;
- a chat interface over unrestricted production data;
- a system that must retain distributed traces.

## 3. Users and Workflows

### Operator

Registers applications, checks source availability, reviews open incidents, opens capsules, and configures model profiles.

### Investigator

Starts from an alert or incident window, reviews the strongest FM, PM, log, topology, and trace-availability evidence, then follows grounded next checks.

### Maintainer

Adds source adapters, updates scoring, runs regression scenarios, compares models, and validates retention/privacy behavior.

### Evaluator

Reproduces the incident lab, inspects objective metrics, compares identical model inputs, and reviews evidence provenance.

## 4. Operational Signal Domains

Each domain has a distinct shape and method. The system must keep the distinction visible in data contracts and the UI.

| Domain | Shape | Required behavior |
|---|---|---|
| Fault management (FM) | discrete alerts/events | define trigger, severity, sequence, and affected scope |
| Performance management (PM) | numeric series | compare baseline and incident values; rank anomalies |
| Application logs | semi-structured text | anonymize, group, count, and retain representatives |
| Topology/configuration | entity graph and change events | align services, pods, namespaces, clusters, and dependencies |
| On-demand traces | temporary queryable spans | probe availability and retrieve only when needed; do not retain raw spans |
| AI reasoning | structured generated text | cite evidence IDs, express uncertainty, and produce actionable checks |

A domain with no selected evidence must not be presented as broken. The UI should distinguish source availability, raw items observed, candidates analyzed, and evidence retained.

## 5. Core Workflow

1. Receive an alert trigger or an explicit application/time window.
2. Resolve the application and source configuration.
3. Query FM, PM, logs, topology, and trace availability for the bounded window.
4. Normalize timestamps, labels, entity identity, and source provenance.
5. Reduce log volume into templates and representative anonymized lines.
6. detect explainable PM anomalies against a baseline.
7. Build the FM timeline and correlate affected entities.
8. Score evidence using visible components.
9. Select a domain-balanced capsule.
10. Generate deterministic investigation paths.
11. Verify every cited evidence ID.
12. Optionally run enabled pretrained models over the exact same capsule input.
13. Persist the capsule, evaluation, provenance, and derived-only archive.
14. Expose the result through CLI, API, Operations, and static report surfaces.

## 6. Capsule Requirements

Every capsule must include:

- stable capsule and incident identity;
- application, namespace, cluster, and incident window;
- FM alert context and timeline;
- selected evidence with evidence IDs and domain labels;
- scoring components and inclusion rationale;
- reduced log templates with anonymized representatives;
- PM anomaly measurements and baseline comparison;
- topology/configuration context when available;
- trace-source availability and retention notes;
- grounded hypotheses;
- contradicting or missing evidence;
- concrete next checks;
- objective reduction and preservation metrics;
- limitations and provenance.

The derived archive must exclude raw logs, full metric exports, credentials, and raw spans.

## 7. Control Plane Requirements

The local control plane uses SQLite and must track:

- application registrations and signal-source configuration;
- current application health;
- incident metadata and raw data volume observed;
- capsule metadata, retained size, and evaluation;
- model profiles, enablement, and token limits.

The control plane must support multiple applications and multiple incidents. Generated lab state lives under `.fcapsule/` and must remain outside Git.

## 8. User Interfaces

### Operations

The Operations view must make these questions answerable at a glance:

- Which applications are tracked?
- Which are healthy or degraded?
- Which FM, PM, log, and trace sources are available?
- When was the last incident?
- Which capsules exist?
- How much raw data was inspected and how much evidence was retained?
- Which models are enabled?
- What is the strongest evidence in a selected capsule?

### Incident Lab

The lab must:

- start empty;
- allow application name, identifier, scenario, request volume, and concurrency configuration;
- run real local traffic;
- expose baseline, injection, alert, capsule, and model stages;
- show progress without blocking the UI;
- keep simulation and capsule creation as separate actions;
- register the simulated application and incident in Operations;
- clearly state that traces are queried on demand and not retained.

## 9. Incident Scenario Requirements

The reference scenario must be more complex than a direct endpoint failure. It uses:

- checkout and inventory HTTP services;
- concurrent baseline and incident traffic;
- a runtime configuration change;
- inventory partition lock contention;
- database-pool saturation;
- checkout retries with a circuit breaker that remains closed;
- retry amplification;
- rising latency and error rate;
- a three-alert FM sequence;
- thousands of structured logs and at least eighteen PM series;
- ephemeral trace-source access.

The evidence must support a tentative chain: lock contention may create dependency latency; checkout retries may amplify load; pool saturation may sustain the failure. The capsule must not claim this chain as proven final root cause without database diagnostics and request traces.

## 10. AI Model Requirements

The core pipeline must work without an external model.

When offline model comparison is enabled:

- all models receive the same compact capsule and prompt;
- provider, model, latency, tokens, parse status, and finish reason are recorded;
- every generated citation is checked against selected evidence;
- scoring covers JSON validity, citation validity, domain coverage, expected-signal coverage, actionability, evidence breadth, and cautious causal language;
- a winner is recorded only when the fixed rubric produces a measurable score delta;
- the evaluation runner may configure model enablement and maximum-token limits outside the operator UI.

The initial comparison profiles are `deepseek-v4-flash` and `deepseek-v4-pro`. The model boundary must remain provider-extensible.

## 11. Evaluation Requirements

Required objective measures:

- raw log lines and bytes;
- selected representative lines;
- log and template reduction;
- estimated raw and capsule tokens;
- token reduction;
- representative signal groups identified, preserved, and missing;
- PM anomaly preservation;
- citation grounding;
- capsule runtime;
- retention-section completeness;
- capsule bytes versus raw bytes;
- model latency, usage, and rubric score when enabled.

Regression cases must define expected diagnostic signal groups without treating them as root-cause truth.

## 12. Privacy and Safety

- Read-only source access is the default.
- Secrets must come from environment variables or external secret stores.
- `.env`, `.fcapsule/`, and generated outputs must not be committed unintentionally.
- IPs, emails, UUID-like values, tokens, passwords, and variable identifiers are masked before representative lines enter capsules.
- Raw spans are never written to capsule storage.
- Model calls receive selected evidence, not unrestricted raw telemetry.
- The system does not execute remediation.

## 13. Deployment Contract

The local version is a reference control plane. A pod deployment should mount configuration and durable metadata storage, expose the web/API port, and receive read-only credentials for configured adapters.

Future live adapters must normalize to the same case contract. Pipeline behavior must not depend on whether the source is a file export, direct HTTP API, webhook trigger, or simulator.

The service may later use PostgreSQL and distributed workers, but SQLite and in-process jobs are the supported local mode.

## 14. Definition of Done

A release is complete when:

- CLI and both web views start from one command;
- a user can register or simulate an application;
- the complex scenario triggers all expected alerts;
- the incident appears in Operations;
- a capsule can be built without an API key;
- enabled models can run over identical input when credentials are available;
- artifacts exclude raw telemetry;
- objective metrics and missing signals are visible;
- automated tests pass;
- both desktop and mobile layouts are visually usable;
- documentation matches current commands, storage, and behavior.
