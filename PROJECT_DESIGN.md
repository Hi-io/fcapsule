# FCAPSule AI Project Design

## Overview

FCAPSule AI preserves the evidence that matters before raw telemetry disappears. It accepts a bounded incident case and orchestrates text reduction, time-series analysis, alert processing, entity resolution, evidence attention, grounded reasoning, verification, and evaluation.

The primary P1 user is an engineer who needs a compact starting point for investigation. The academic goal is to demonstrate Template 4.1, orchestration of AI models and analysis methods toward a shared goal, rather than a single chatbot over sampled logs.

## P1 Scope

P1 is a local CLI with file-based inputs. It implements the complete logical workflow and stable extension interfaces while excluding live integrations, authentication, remediation, and a web UI.

## Architecture

```text
case files
   |
   v
schema validation -> entity resolution -> anonymization
   |                                         |
   +-> log reduction ------------------------+
   +-> metric anomaly analysis --------------+-> evidence attention
   +-> alert timeline -----------------------+          |
                                                      v
                                          hypothesis generation
                                                      |
                                                      v
                                                verification
                                                      |
                                                      v
                                   capsule + evaluation + archive
```

## Components

### Case loader

Loads `metadata.yaml`, alerts, metrics, logs, and optional expected notes. It normalizes timestamps and rejects malformed required input with actionable messages.

### Entity resolver

Matches service, namespace, cluster, pod, and CNCC UUID across domains. Mismatches are retained as warnings rather than silently discarded.

### Log reducer

Masks dynamic tokens and groups messages by normalized template. It records counts, severity, temporal range, representative lines, and proximity to the alert.

### Metrics analyzer

Splits each series into baseline and incident segments relative to the first alert. It calculates robust z-score, percentage change, direction, and an explainable anomaly score.

### Alert timeline

Normalizes one or more alerts and combines them with selected log and metric events into chronological context.

### Evidence attention engine

Normalizes cross-domain features and records every scoring component. P1 prioritizes severity, anomaly, temporal proximity, entity match, rarity, and relevance while penalizing repetitive low-value logs.

### Reasoning and verification

The default deterministic reasoner produces cautious investigation paths from selected evidence. The verifier rejects nonexistent evidence IDs and reduces confidence when support is weak or required telemetry is missing. An optional LLM client boundary can replace generation later without changing upstream processing.

### Evaluation

The evaluator compares the selected capsule against keyword and time-window baselines. It reports compression, template reduction, token reduction, signal preservation, anomaly preservation, grounding, runtime, and retention survivability.

## Design Rationale

- **Local-first:** reproducible without company systems or credentials.
- **Explainable methods:** P1 exposes intermediate scores instead of hiding selection behind one model call.
- **Deterministic fallback:** the whole system works without an external LLM.
- **Evidence IDs:** generated hypotheses can be mechanically verified.
- **Separate raw and derived data:** outputs preserve evidence without copying the complete source bundle into the archive.
- **Adapter boundaries:** future live sources can produce the same `CaseBundle` contract.

## Template Alignment

P1 orchestrates distinct domains:

1. text/log template analysis;
2. metric/time-series anomaly analysis;
3. fault/alert event processing;
4. infrastructure entity resolution;
5. grounded hypothesis generation and verification.

Each stage changes or enriches the shared evidence representation, and downstream stages depend on earlier outputs. This is an orchestrated pipeline, not unrelated parallel calls.

## Limitations

P1 cannot validate real production usefulness from one synthetic case. Statistical anomaly scores depend on the supplied window, log templates use deterministic masking, and hypotheses are investigation suggestions rather than causal conclusions.

## Evolution

The schema and adapters are intentionally isolated so live Prometheus, OpenSearch, and Alertmanager inputs can be added without replacing the attention, reasoning, capsule, or evaluation layers.
