# FCAPSule: Concept

**Status:** durable product purpose and research question. For current behavior, use the [project guide](FCAPSule_AI_Project_Guide.md); for the changing implementation, use the [implementation proposal](FCAPSule_AI_Implementation_Proposal.md). The [original long-form concept](docs/history/initial_concept.md) is retained as historical context, not a shipped-feature inventory.

## The Problem

Monitoring teams see alerts but often do not own the affected applications. The observations needed to investigate are spread across alert rules, log search, metrics, workload state and configuration. High-volume telemetry has finite and sometimes short retention; by the time a recurring issue is reviewed, the decisive source window may be gone. A dashboard can display each source, but it does not automatically decide which observations matter together or preserve the reasoning that connected them.

## The Idea

**Turn an alert into an inspectable investigation that can be remembered.** FCAPSule should connect the incident's available telemetry, preserve a compact evidence capsule, and help an engineer decide what to check next. An AI investigator should explore only permitted read-only observations, distinguish evidence from inference, consider alternatives and explain uncertainty. The operator should be able to inspect its steps and add a missing observation.

When a related incident returns, the investigator should be able to consult eligible earlier capsules. This is operational memory through retrieval of retained evidence, not continual training or an assertion that the model has learned a universal cause.

FCAPSule's name draws on the FCAPS management tradition and the capsule that carries selected incident context. The current product concentrates on fault events, performance, logs and supported configuration; the name does not imply full accounting or security-management coverage.

## Why AI Belongs Here

Deterministic software can collect, normalize, bound and rank evidence reliably. It is less suited to choosing a diagnostic question when several plausible failure paths compete across different data shapes. The model's job is not to paraphrase an alert. It should decide which supported observation could discriminate among explanations, review the result, and propose a concrete next check with evidence references.

The model remains constrained by source availability, context budgets, allowlisted tools and grounding checks. A readable answer is not proof of causality. Engineers retain responsibility for operational decisions and remediation.

## What Success Means

- A responder identifies the affected resource, observed symptoms, likely mechanism, supporting and contradicting evidence, uncertainty and a useful next check faster than by manually joining the same sources.
- The capsule remains inspectable and exportable after the original source window expires, provided relevant observations were captured and FCAPSule retains the capsule.
- A related future incident can use a clearly identified earlier capsule as additional context without silently promoting an old hypothesis to fact.
- The system works when a model is unavailable: deterministic capture and reports survive, while the AI investigation is marked unavailable or incomplete.
- Evaluation separates capture success from diagnosis quality, compares models on identical evidence and records incorrect as well as successful results.
- Resource use, source queries, retained data and provider tokens are bounded and observable.

## Data and Model Domains

The core operational inputs are fault events, numeric performance series, semi-structured logs, and Kubernetes workload/configuration facts. These are different **telemetry domains**, not interchangeable with the media modalities in the academic brief. Operator-supplied screenshots and spoken notes add separate **image** and **audio** domains where they have a real investigative purpose.

The present design uses a primary pretrained language model for bounded investigation and optional pretrained vision and speech specialists to extract observations from operator-supplied media. Deterministic telemetry analysis is not mislabeled as a separate pretrained model. The actual enabled providers, capability gates and evaluation status belong in the [current product guide](FCAPSule_AI_Project_Guide.md) and [multimodal evidence guide](docs/multimodal_evidence.md).

## Boundaries

FCAPSule complements existing observability platforms; it is not a replacement telemetry store, an autonomous remediator, a guaranteed root-cause engine or a general chat interface. It cannot reconstruct information it never captured. Longer-lived capsules can support a future decision to change raw retention, but savings must be measured in the target environment and retention policies must be set by its operators.

The [evaluation plan](EVALUATION_PLAN.md) states how to test usefulness, retention continuity, model roles, cost and failure modes. The [roadmap](ROADMAP.md) separates future hardening from this stable goal.
