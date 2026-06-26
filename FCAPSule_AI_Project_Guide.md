# FCAPSule AI — Project Design and P1 Development Guide

**Project:** FCAPSule AI  
**Full title:** FCAPSule AI: A Multimodal Telemetry Attention Engine for Cloud Incident Evidence  
**Current target:** Prototype 1 (P1)  
**Long-term target:** Evolve the same repository into the final university project and demo system  
**Primary audience for this document:** Codex / AI coding agent / future developer  
**Secondary audience:** project author, reviewers, supervisors, future contributors

---

## 0. Executive Summary

FCAPSule AI is an evidence-selection and preservation system for cloud-native incident investigation. It is designed for environments where applications produce large amounts of observability telemetry: logs, performance metrics, fault alerts, and infrastructure metadata. In sectors such as telecom, raw telemetry volume can be extremely large, and retention constraints make it impossible to store all logs and metrics indefinitely. Troubleshooting may last days or weeks, while raw data may be deleted, rotated, or become expensive to query.

The purpose of FCAPSule AI is to generate a compact, structured, investigation-ready **evidence capsule** from raw or semi-raw telemetry. The capsule should preserve the most useful evidence before raw telemetry expires, reduce noise, support human investigation, and provide grounded context for future AIOps agents.

Prototype 1 should **not** attempt to be a production-ready system. P1 should prove the core concept:

> Given a prepared incident case containing alert, log, metric, and metadata files, can FCAPSule AI reduce noisy telemetry into a compact evidence capsule while preserving important investigation signal?

The first implementation should use a CLI and file-based inputs. Live Prometheus, OpenSearch, Kafka, and Alertmanager integrations are part of the roadmap, not mandatory for P1. A simple static dashboard and optional DeepSeek model comparison may be included in P1 because they make the evaluation easier to inspect without changing the local-first architecture.

---

## 1. Project Positioning

### 1.1 What FCAPSule AI is

FCAPSule AI is a **multidomain telemetry attention engine**. It consumes multiple telemetry sources and produces a compact evidence capsule.

In this project, a domain means an operational telemetry signal family. It is analogous to how text, audio, and image are different AI modalities, but FCAPSule P1 does not require image generation. The P1 domains are:

- **Fault events:** alert and incident event streams.
- **Log text:** semi-structured application logs.
- **Time-series metrics:** numeric measurements over time.
- **Topology metadata:** service, namespace, pod, cluster, CNCC UUID, and related entity labels.
- **LLM reasoning:** generated, evidence-grounded interpretation and next-check suggestions.

It should:

- load prepared incident case files;
- align evidence around common entities such as service, namespace, pod, cluster, and CNCC UUID or equivalent labels;
- reduce raw logs into templates and representative lines;
- identify relevant or anomalous metrics;
- build an alert/fault timeline;
- rank evidence across logs, metrics, and alerts;
- generate grounded investigation hypotheses;
- verify whether the hypotheses are supported by actual evidence;
- generate a final capsule in Markdown and structured machine-readable formats;
- produce evaluation metrics to compare against simple baselines;
- optionally compare `deepseek-v4-flash` and `deepseek-v4-pro` on the same capsule input;
- render a local dashboard for review.

### 1.2 What FCAPSule AI is not, especially in P1

P1 is not:

- a production observability platform;
- a replacement for Prometheus, OpenSearch, Grafana, Alertmanager, or Kafka;
- a fully automated root cause analysis system;
- a real-time incident response agent;
- an auto-remediation tool;
- a polished web application;
- a full ingestion pipeline.

The project should avoid claiming guaranteed RCA accuracy unless ground-truth cases become available. The safer and more accurate claim is:

> FCAPSule AI generates ranked, evidence-grounded investigation hypotheses and preserves compact incident evidence.

### 1.3 Why this matters

The motivation is not only faster incident investigation. A major motivation is **data retention under storage constraints**.

Large-scale systems can generate huge volumes of telemetry. It is not realistic to store every log and metric forever for every application. However, investigations may continue after raw telemetry has expired. FCAPSule AI aims to preserve the operationally useful evidence in a compact form so that engineers can continue investigating even when raw logs are no longer available.

This gives the project two complementary value propositions:

1. **Investigation acceleration:** reduce telemetry overload and help engineers see what matters first.
2. **Evidence preservation:** retain compact, useful incident evidence under telemetry retention constraints.

---

## 2. University Template Alignment

### 2.1 Chosen template

The chosen university template is:

**Template 4.1 — Orchestrating AI Models to Achieve a Goal**

The project must clearly show that multiple models or AI-assisted methods are orchestrated into a workflow. Do not implement a single chatbot and call it orchestration.

### 2.2 Goal under the template

The goal is:

> Orchestrate multiple AI models and analysis methods to generate compact, grounded, and useful incident evidence capsules from multimodal observability telemetry.

### 2.3 Required domains / model families

The project should include at least three distinct domains. P1 should implement at least three of the following:

1. **Logs / text telemetry domain**
   - log masking;
   - template extraction;
   - severity detection;
   - frequency analysis;
   - representative log selection;
   - optional embeddings or LLM semantic scoring.

2. **Performance metrics / time-series telemetry domain**
   - z-score;
   - robust z-score;
   - moving average;
   - baseline comparison;
   - percentage change;
   - anomaly window detection.

3. **Fault alerts / event-stream domain**
   - alert parsing;
   - severity ranking;
   - timeline construction;
   - entity matching with logs and metrics.

4. **Infrastructure / topology context domain**
   - service, namespace, pod, cluster mapping;
   - CNCC UUID or equivalent label resolution;
   - matching evidence across telemetry sources.

5. **LLM-based reasoning domain**
   - hypothesis generation;
   - explanation;
   - missing evidence identification;
   - suggested next checks;
   - final capsule writing.

6. **LLM or rule-based verification**
   - claim grounding;
   - evidence ID validation;
   - hallucination reduction;
   - confidence adjustment.

### 2.4 How to exceed the template requirements

To target a high mark, the project should demonstrate:

- clear orchestration, not just parallel model calls;
- justification for each model or method;
- comparison against simpler baselines;
- objective and subjective evaluation;
- clear discussion of limitations;
- iterative design and testing;
- evidence that the system is useful for real users in the domain;
- clean documentation and reproducible case examples.

---

## 3. P1 Scope

### 3.1 P1 objective

P1 should answer this question:

> Can FCAPSule AI reduce a prepared noisy incident telemetry bundle into a compact evidence capsule while preserving important investigation signal?

### 3.2 Included in P1

Implement:

- CLI interface;
- file-based case input;
- schema validation;
- case loading;
- entity resolution;
- log reduction;
- metric anomaly analysis;
- alert timeline building;
- evidence ranking;
- hypothesis generation;
- hypothesis verification;
- capsule generation;
- objective evaluation metrics;
- baseline comparison;
- optional DeepSeek same-input comparison;
- static local dashboard;
- documentation.

### 3.3 Excluded from P1

Do not implement unless everything else is complete:

- real Kafka consumer;
- real Alertmanager webhook trigger;
- real Prometheus adapter;
- real OpenSearch adapter;
- production deployment;
- authentication;
- production web UI;
- Grafana dashboard generation;
- parallel agent execution;
- automatic remediation.

### 3.4 Roadmap after P1

The repository should be designed so that future versions can add:

| Phase | Goal |
|---|---|
| P2 | Real Prometheus and OpenSearch adapters |
| P3 | Alertmanager webhook trigger |
| P4 | More advanced evidence attention scoring |
| P5 | Richer web UI for case review |
| P6 | Retention-aware capsule store |
| P7 | More case studies and user feedback |
| P8 | Optional integration with Grafana dashboards |
| P9 | Optional downstream AIOps/RCA agent |

Do not hard-code P1 in a way that blocks these future phases.

---

## 4. Expected Repository Structure

Use a clean Python project structure.

```text
fcapsule-ai/
  README.md
  PROJECT_DESIGN.md
  ROADMAP.md
  EVALUATION_PLAN.md
  DATA_SCHEMA.md
  PROMPTS.md
  CHANGELOG.md
  pyproject.toml
  requirements.txt
  .gitignore
  .env.example

  fcapsule/
    __init__.py
    cli.py
    config.py

    models/
      __init__.py
      schemas.py

    io/
      __init__.py
      case_loader.py
      output_writer.py
      archive_writer.py

    processing/
      __init__.py
      entity_resolver.py
      log_reducer.py
      metrics_analyzer.py
      alert_timeline.py
      anonymizer.py

    attention/
      __init__.py
      evidence_scorer.py
      evidence_selector.py

    reasoning/
      __init__.py
      llm_client.py
      hypothesis_generator.py
      hypothesis_verifier.py
      capsule_writer.py
      prompts.py

    evaluation/
      __init__.py
      metrics.py
      baselines.py
      rubric.py
      report.py

    adapters/
      __init__.py
      prometheus_adapter.py      # future, can be stubbed
      opensearch_adapter.py      # future, can be stubbed
      alertmanager_adapter.py    # future, can be stubbed

  cases/
    case_001/
      alert.json
      prometheus_metrics.json
      opensearch_logs.json
      metadata.yaml
      expected_notes.md

  outputs/
    .gitkeep

  tests/
    test_case_loader.py
    test_entity_resolver.py
    test_log_reducer.py
    test_metrics_analyzer.py
    test_evidence_scorer.py
    test_capsule_writer.py

  docs/
    architecture.md
    p1_usage.md
    p1_evaluation.md
    data_privacy.md
    design_decisions.md
```

The developer should create or rewrite documentation files so that they match the current project design. Avoid stale docs.

---

## 5. P1 Input Format

### 5.1 Case folder

Each case should be self-contained.

```text
cases/case_001/
  alert.json
  prometheus_metrics.json
  opensearch_logs.json
  metadata.yaml
  expected_notes.md
```

### 5.2 `metadata.yaml`

Example:

```yaml
case_id: case_001
case_title: High log volume and suspected indexing delay
service: checkout-service
cluster: prod-cluster-a
namespace: checkout
cncc_uuid: cncc-12345
window:
  start: "2026-06-21T09:30:00Z"
  end: "2026-06-21T10:30:00Z"
timezone: UTC
telemetry_sources:
  logs: opensearch_logs.json
  metrics: prometheus_metrics.json
  alert: alert.json
fields:
  log_time_field: "@timestamp"
  log_message_field: "message"
  log_level_field: "level"
  service_label: "cncc_uuid"
privacy:
  anonymized: true
notes:
  - "Prepared sample case for P1."
```

### 5.3 `alert.json`

Example:

```json
{
  "alertname": "HighLogVolume",
  "status": "firing",
  "severity": "warning",
  "startsAt": "2026-06-21T10:00:00Z",
  "endsAt": null,
  "labels": {
    "service": "checkout-service",
    "namespace": "checkout",
    "cluster": "prod-cluster-a",
    "pod": "checkout-api-7c9d",
    "cncc_uuid": "cncc-12345"
  },
  "annotations": {
    "summary": "Log volume increased above baseline",
    "description": "The application is producing more logs than expected."
  }
}
```

### 5.4 `prometheus_metrics.json`

Use a simple export format compatible with later Prometheus query results.

```json
{
  "window": {
    "start": "2026-06-21T09:30:00Z",
    "end": "2026-06-21T10:30:00Z"
  },
  "series": [
    {
      "metric": "container_memory_working_set_bytes",
      "labels": {
        "pod": "checkout-api-7c9d",
        "namespace": "checkout"
      },
      "values": [
        ["2026-06-21T09:30:00Z", 512000000],
        ["2026-06-21T10:00:00Z", 950000000]
      ]
    }
  ]
}
```

### 5.5 `opensearch_logs.json`

Example:

```json
{
  "hits": [
    {
      "@timestamp": "2026-06-21T10:01:22Z",
      "level": "ERROR",
      "message": "Failed to connect to 10.0.0.3 after 3 retries",
      "service": "checkout-service",
      "namespace": "checkout",
      "pod": "checkout-api-7c9d",
      "cluster": "prod-cluster-a",
      "cncc_uuid": "cncc-12345"
    }
  ]
}
```

---

## 6. CLI Requirements

### 6.1 Main command

Implement:

```bash
fcapsule investigate --case ./cases/case_001 --out ./outputs/case_001
```

Expected behavior:

1. Load the case.
2. Validate schemas.
3. Resolve entities.
4. Reduce logs.
5. Analyze metrics.
6. Build alert timeline.
7. Score and select evidence.
8. Generate hypotheses.
9. Verify hypotheses.
10. Write capsule and evaluation outputs.
11. Create a zip archive.

### 6.2 Evaluation command

Implement:

```bash
fcapsule evaluate --case ./cases/case_001 --output ./outputs/case_001
```

This should compute or recompute objective evaluation metrics and baseline comparisons.

### 6.3 Optional debug command

Implement if easy:

```bash
fcapsule inspect --case ./cases/case_001
```

This should print case metadata, entity matches, log counts, metric counts, and alert info.

---

## 7. Core Pipeline Design

### 7.1 Case Loader

Responsibilities:

- read all case files;
- validate required fields;
- parse timestamps;
- normalize field names;
- return a structured `CaseBundle` object.

Failure behavior:

- if required files are missing, return a clear error;
- if optional files are missing, continue with warnings;
- if timestamps are invalid, fail with explanation.

### 7.2 Entity Resolver

Responsibilities:

- identify primary service, cluster, namespace, pod, and CNCC UUID;
- align entities across alert, logs, and metrics;
- detect mismatches;
- generate entity coverage summary.

Output example:

```json
{
  "primary_entity": "checkout-service",
  "matched_labels": {
    "cncc_uuid": "cncc-12345",
    "namespace": "checkout",
    "cluster": "prod-cluster-a"
  },
  "coverage": {
    "logs_found": true,
    "metrics_found": true,
    "alert_found": true
  },
  "warnings": []
}
```

### 7.3 Anonymizer

Even if P1 uses prepared data, include anonymization utilities.

Mask:

- IP addresses;
- UUIDs;
- long IDs;
- tokens;
- secrets;
- emails;
- hostnames if needed;
- customer identifiers.

Output must preserve structure while removing sensitive values.

Example:

```text
Failed to connect to 10.0.0.3 after 3 retries
```

becomes:

```text
Failed to connect to <IP> after <NUM> retries
```

### 7.4 Log Reducer

Responsibilities:

- normalize log messages;
- group logs into templates;
- count frequency;
- compute severity distribution;
- compute first seen and last seen;
- compute temporal proximity to alert;
- select representative lines;
- identify high-volume low-value templates;
- identify rare or high-severity templates.

For P1, use a simple Drain-inspired approach:

1. mask variable tokens;
2. tokenize message;
3. group by normalized template string;
4. calculate statistics;
5. select top templates based on evidence score.

Output fields:

```json
{
  "template_id": "log_template_007",
  "template": "Failed to connect to <IP> after <NUM> retries",
  "count": 18342,
  "volume_percentage": 31.2,
  "levels": {"ERROR": 18342},
  "first_seen": "2026-06-21T09:58:12Z",
  "last_seen": "2026-06-21T10:28:33Z",
  "representative_lines": [
    "2026-06-21T10:01:22Z Failed to connect to <IP> after <NUM> retries"
  ]
}
```

### 7.5 Metrics Analyzer

Responsibilities:

- parse metric series;
- calculate baseline statistics;
- detect spikes or drops;
- rank anomalous metrics;
- link metrics to entities;
- summarize metric changes.

Use explainable methods for P1:

- z-score;
- robust z-score;
- percentage change;
- moving average comparison;
- alert-window proximity.

Output example:

```json
{
  "metric_id": "metric_003",
  "metric": "container_memory_working_set_bytes",
  "entity": "checkout-api-7c9d",
  "anomaly_score": 0.87,
  "reason": "Memory increased 85% near alert time compared with previous baseline."
}
```

### 7.6 Alert Timeline Builder

Responsibilities:

- parse alert time;
- include alert severity and labels;
- build chronological timeline;
- support multiple future alerts;
- connect alert labels with resolved entities.

Output example:

```markdown
09:57 — Warning: OpenSearch indexing delay increased
10:00 — Warning: HighLogVolume fired for checkout-service
10:05 — Error log spike detected
```

### 7.7 Evidence Attention Engine

This is the core of P1.

Responsibilities:

- combine log templates, metric anomalies, alerts, and metadata;
- score evidence items;
- rank evidence;
- select top evidence for the capsule;
- explain why evidence was selected;
- summarize discarded evidence.

Suggested scoring formula:

```text
attention_score =
  severity_weight
+ anomaly_score
+ temporal_proximity
+ entity_match_score
+ rarity_score
+ semantic_relevance
- repetition_penalty
```

Do not make this a black box in P1. Store intermediate scores in `evidence.json`.

Evidence item example:

```json
{
  "evidence_id": "ev_log_007",
  "type": "log_template",
  "source_id": "log_template_007",
  "score": 0.91,
  "why_selected": "High-frequency ERROR pattern near the alert window for the affected pod.",
  "linked_entities": ["checkout-service", "checkout-api-7c9d"],
  "time_range": {
    "start": "2026-06-21T09:58:12Z",
    "end": "2026-06-21T10:28:33Z"
  }
}
```

### 7.8 Hypothesis Generator

Use an LLM only after evidence has been selected.

Input:

- alert context;
- selected evidence;
- metric anomalies;
- log summaries;
- missing evidence notes.

Output:

```json
[
  {
    "hypothesis_id": "hyp_001",
    "hypothesis": "A log volume spike may have contributed to indexing delay.",
    "confidence": 0.74,
    "supporting_evidence": ["ev_log_007", "ev_metric_003", "ev_alert_001"],
    "contradicting_evidence": [],
    "missing_evidence": ["Kafka consumer lag", "OpenSearch indexing queue metrics"],
    "next_checks": [
      "Check Kafka consumer lag for the affected topic.",
      "Check OpenSearch indexing queue metrics during the alert window."
    ]
  }
]
```

Important: hypotheses must be phrased as plausible investigation paths, not absolute conclusions.

### 7.9 Hypothesis Verifier

Responsibilities:

- check that every hypothesis cites evidence IDs;
- check that cited evidence IDs exist;
- identify unsupported claims;
- adjust confidence if necessary;
- add warnings for weak reasoning.

Output example:

```json
{
  "hypothesis_id": "hyp_001",
  "verdict": "plausible",
  "adjusted_confidence": 0.70,
  "verification_notes": [
    "All cited evidence IDs exist.",
    "The hypothesis is plausible but Kafka lag evidence is missing."
  ]
}
```

### 7.10 Capsule Writer

Generate `capsule.md` with this structure:

```markdown
# FCAPSule AI Evidence Capsule

## 1. Case Summary

## 2. Alert Context

## 3. Telemetry Window

## 4. Incident Timeline

## 5. Selected Evidence

## 6. Log Compression Summary

## 7. Metric Anomalies

## 8. Ranked Investigation Hypotheses

## 9. Missing Evidence

## 10. Suggested Next Steps

## 11. Retention Note

## 12. Evaluation Summary
```

The Retention Note is important. It should answer:

> If raw telemetry expires, what useful evidence has this capsule preserved?

---

## 8. Evaluation Design

Evaluation is central to the project. P1 should be evaluated during development, not only at the end.

### 8.1 Main evaluation question

> Can FCAPSule AI reduce noisy incident telemetry into compact evidence capsules while preserving useful investigation signal?

### 8.2 Baselines

Implement or manually compare against:

1. **Raw telemetry baseline**
   - User receives raw logs, metrics, and alert data.

2. **Keyword filter baseline**
   - Select logs using simple filters: `ERROR`, `WARN`, pod name, service name, alert name.

3. **Time-window sample baseline**
   - Select fixed number of logs around the alert timestamp.

4. **Single LLM baseline**
   - Send a naive sampled telemetry subset to an LLM without structured evidence selection.

### 8.3 Objective metrics

Generate `evaluation.json` with:

```json
{
  "raw_log_lines": 100000,
  "selected_log_lines": 120,
  "log_compression_ratio": 0.9988,
  "raw_log_bytes": 50000000,
  "capsule_bytes": 120000,
  "token_reduction_percentage": 0.992,
  "important_signal_preservation": 0.91,
  "hypothesis_grounding_score": 0.86,
  "runtime_seconds": 94,
  "retention_survivability_score": 0.88
}
```

Metric definitions:

- **Log compression ratio:** reduction from raw lines/bytes to selected evidence.
- **Template reduction ratio:** raw messages vs grouped templates.
- **Token reduction:** estimated raw input tokens vs final evidence tokens.
- **Signal preservation:** percentage of important evidence items preserved.
- **Metric anomaly preservation:** whether anomalous metrics are included.
- **Hypothesis grounding score:** percentage of LLM claims linked to evidence IDs.
- **Runtime:** total processing time.
- **Retention survivability score:** whether the capsule contains enough sections to remain useful after raw data expires.

### 8.4 Signal preservation method

Since ground-truth RCA may not be available, define “important signal” operationally.

Important signal can include:

- high-severity alert;
- ERROR or WARN templates;
- rare templates;
- templates that spike near alert time;
- metrics with anomaly scores above threshold;
- affected service/pod/cluster labels;
- known suspicious keywords;
- notes from `expected_notes.md`.

`expected_notes.md` can be used as a lightweight manual reference for each case.

### 8.5 Subjective evaluation

Create a manual review form in `docs/p1_evaluation.md`.

Ask reviewers to compare baseline output and FCAPSule output.

Use 1–5 Likert scores:

| Question | Scale |
|---|---|
| How clear is the capsule? | 1–5 |
| How useful is it for starting investigation? | 1–5 |
| How trustworthy are the hypotheses? | 1–5 |
| Does it miss important evidence? | 1–5 |
| Are the suggested next steps actionable? | 1–5 |
| Would this save time compared with raw logs? | 1–5 |

Include free-text questions:

- What was most useful?
- What was confusing?
- What evidence seemed missing?
- Would you use this during an investigation?
- What should be improved in the next iteration?

### 8.6 Development testing

Add tests for:

- input schema validation;
- timestamp parsing;
- log anonymization;
- template grouping;
- metric anomaly scoring;
- evidence score calculation;
- evidence ID integrity;
- capsule file creation;
- evaluation metrics.

Add regression tests using the same sample case so that changes do not break previous behavior.

---

## 9. Documentation Requirements

Codex/developer should create or rewrite the following docs.

### 9.1 `README.md`

Must include:

- project description;
- what problem it solves;
- quickstart;
- example command;
- example output;
- P1 limitations;
- roadmap.

### 9.2 `PROJECT_DESIGN.md`

Must include:

- project overview;
- template alignment;
- domain and users;
- architecture;
- components;
- design rationale;
- P1 scope;
- roadmap.

### 9.3 `DATA_SCHEMA.md`

Must document:

- case folder structure;
- `metadata.yaml`;
- `alert.json`;
- `prometheus_metrics.json`;
- `opensearch_logs.json`;
- output schemas.

### 9.4 `EVALUATION_PLAN.md`

Must document:

- main evaluation question;
- baselines;
- objective metrics;
- subjective review method;
- scoring rubric;
- limitations.

### 9.5 `PROMPTS.md`

Must include:

- hypothesis generation prompt;
- verification prompt;
- capsule writing prompt;
- constraints requiring evidence IDs;
- no unsupported RCA claims.

### 9.6 `ROADMAP.md`

Must show:

- P1 implementation;
- P2 live adapters;
- P3 Alertmanager trigger;
- P4 better scoring;
- P5 UI;
- P6 retention-aware store;
- P7 final evaluation.

### 9.7 `docs/data_privacy.md`

Must explain:

- anonymization;
- no secrets;
- no customer identifiers;
- safe sample data;
- how to use synthetic cases.

---

## 10. LLM Prompting Requirements

Prompts must enforce grounding.

### 10.1 Hypothesis generation rules

The LLM must:

- only use provided evidence;
- cite evidence IDs;
- avoid absolute root cause claims;
- state missing evidence;
- generate ranked hypotheses;
- include next checks;
- include confidence with explanation.

The LLM must not:

- invent metrics;
- invent logs;
- invent services;
- claim root cause without evidence;
- ignore missing data.

### 10.2 Hypothesis verifier rules

The verifier must:

- check evidence IDs exist;
- identify unsupported claims;
- reduce confidence for weak evidence;
- flag missing evidence;
- produce verification notes.

### 10.3 Capsule writing rules

The capsule writer must:

- be concise but complete;
- include evidence IDs;
- clearly separate observed facts from hypotheses;
- include missing evidence;
- include retention note;
- include evaluation summary.

---

## 11. Success Criteria for P1

P1 is successful if it can:

1. load a prepared case folder;
2. parse alert, log, metric, and metadata files;
3. align telemetry by entity and time window;
4. reduce logs into templates and representative evidence;
5. detect relevant metric anomalies;
6. rank evidence across logs, metrics, and alerts;
7. generate evidence-grounded hypotheses;
8. verify hypotheses;
9. produce a structured capsule;
10. produce objective evaluation metrics;
11. compare against at least one baseline;
12. preserve enough information to support later investigation under retention constraints.

---

## 12. Contingency Plans

| Risk | Contingency |
|---|---|
| Real data cannot be used | Use anonymized or synthetic sample cases |
| Metrics are incomplete | Focus P1 on logs + alerts first |
| LLM API is unavailable | Use local model or stub output for pipeline testing |
| LLM output hallucinates | Enforce evidence IDs and verifier step |
| Log parsing is weak | Improve masking, use stricter templates, manually inspect templates |
| Project takes too long | Prioritize loader, reducer, attention engine, capsule writer, evaluation |
| No reviewers available | Use structured self-review and peer feedback |
| Live integrations are difficult | Keep adapters stubbed until P2/P3 |

---

## 13. Implementation Priorities

Build in this order:

1. Repository structure and docs.
2. Case schemas.
3. Case Loader.
4. Entity Resolver.
5. Log Reducer.
6. Metrics Analyzer.
7. Alert Timeline Builder.
8. Evidence Attention Engine.
9. Capsule Writer without LLM.
10. Evaluation metrics.
11. Hypothesis Generator.
12. Hypothesis Verifier.
13. Baselines.
14. Zip archive output.
15. Documentation polish.

This order ensures that the project works even before the LLM reasoning is added.

---

## 14. Example Final CLI Output

```text
$ fcapsule investigate --case ./cases/case_001 --out ./outputs/case_001

FCAPSule AI — Investigation started
Case: case_001
Service: checkout-service
Window: 2026-06-21T09:30:00Z → 2026-06-21T10:30:00Z

Loaded:
- Alerts: 1
- Log lines: 100000
- Metric series: 18

Results:
- Log templates generated: 245
- Selected evidence items: 32
- Hypotheses generated: 4
- Hypotheses verified: 4

Evaluation:
- Log compression ratio: 99.88%
- Token reduction: 99.2%
- Signal preservation: 91.0%
- Grounded claims: 86.0%
- Retention survivability: 88.0%

Output written to: ./outputs/case_001/fcapsule_case_001.zip
```

---

## 15. Final Notes for Codex / AI Coding Agent

When implementing this project:

- prioritize clarity over cleverness;
- keep P1 simple and reproducible;
- avoid overengineering live integrations;
- document every assumption;
- keep outputs human-readable and machine-readable;
- ensure every generated hypothesis links to evidence IDs;
- keep raw data and final capsule separate;
- include anonymization utilities from the start;
- write tests for each module;
- update documentation whenever implementation choices change;
- do not claim final root cause unless the evidence supports it;
- preserve the roadmap so this P1 can evolve into the final version.

The core idea is:

> FCAPSule AI is not trying to store everything or solve everything. It is trying to preserve the evidence that matters before raw telemetry disappears.
