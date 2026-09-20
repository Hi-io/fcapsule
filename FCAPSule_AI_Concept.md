# FCAPSule AI - Concept Guide

> **Stable concept document:** This file defines the long-lived problem, purpose, and research framing. Current product requirements are in `FCAPSule_AI_Project_Guide.md`.

> **Reading this document:** The research ambitions, proposed pretrained model roles, illustrative workflows and MVP acceptance ideas below preserve the original concept. They are not a shipped-feature inventory. The current product uses deterministic log/PM selection plus one optional runtime LLM assessment, with offline same-input comparisons. Live Prometheus, OpenSearch and Kubernetes configuration capture are implemented; live traces and separate pretrained log/time-series models are not. See the current guide and privacy policy for actual UI and on-disk capture behavior.

**Project subtitle:** A Multimodal Telemetry Attention Engine for Cloud Incident Evidence

**Public tagline:** *A flight recorder for cloud incidents.*

**Academic framing:** Orchestrating pre-trained AI models to generate compact, investigation-ready evidence capsules from FCAPS-style telemetry.

**Document status:** Stable concept guide. This file describes what the project is trying to achieve, why it matters, what problem it solves, and how success should be judged. Implementation details, schemas, architecture and model choices live in `FCAPSule_AI_Implementation_Proposal.md` because they may change during development.

---

## 1. Executive Summary

FCAPSule AI is an AI orchestration project designed to solve a specific problem in modern observability: when large-scale cloud systems degrade, they generate too much telemetry for humans or AI agents to inspect directly. Logs, metrics, fault alerts, Kafka delays, OpenSearch indexing delays, Kubernetes metadata, and dashboards all contain useful evidence, but the useful signal is buried inside massive noisy streams.

The project does **not** try to be another generic AI SRE chatbot or a full root-cause analysis platform. Instead, it focuses on the earlier and more fundamental bottleneck:

> **Before humans or AI agents can reason about an incident, someone has to select the right evidence.**

FCAPSule AI generates an **Incident Evidence Capsule**: a compact, explainable package containing the most relevant log patterns, anomalous metrics, fault timeline, affected infrastructure context, telemetry delay indicators, and suggested next investigation steps.

The project is inspired by the FCAPS network management model: Fault, Configuration, Accounting/Administration, Performance, and Security. In the first implementation, the practical focus is mainly on:

- **Fault:** alerts, faults, severity, fault timelines.
- **Performance:** Prometheus metrics, metric anomalies, resource saturation, latency/error signals.
- **Logs:** OpenSearch/Kafka logs, log templates, log volume, log delays, repeated patterns.
- **Configuration / Context:** Kubernetes metadata, app name, namespace, cluster, pod, service, stage, owners if available.

The final system should demonstrate orchestration of multiple pre-trained models across different data domains, as required by the University of London CM3020 Artificial Intelligence template 4.1: **Orchestrating AI models to achieve a goal**.

---

## 2. Core Project Idea

### 2.1 One-sentence version

**FCAPSule AI turns noisy production telemetry into compact evidence capsules for investigation, with pretrained model orchestration as a research direction.**

### 2.2 Product-style explanation

When production gets noisy, engineers often face millions of logs, hundreds of metrics, and many fault alerts. FCAPSule AI acts like a flight recorder for cloud incidents: it captures the important evidence around an event and packages it into a small, readable, AI-grounded investigation capsule.

### 2.3 Academic research question

> **Can orchestrated pre-trained AI models select and summarize useful multimodal telemetry evidence while preserving diagnostic signal and reducing investigation context size?**

Alternative research question:

> **How can multi-model AI orchestration transform large-scale FCAPS telemetry into compact evidence capsules that are useful for human and AI-assisted incident investigation?**

### 2.4 Problem being solved

Observability systems collect huge amounts of telemetry, but large volume does not automatically produce understanding. During incidents, engineers need to know:

- What changed?
- Which metrics became abnormal?
- Which fault alerts appeared first?
- Which log patterns dominated the incident window?
- Were logs delayed or missing?
- Which clusters/pods/services were affected?
- What evidence is worth reading first?

FCAPSule AI addresses this by constructing a compact evidence pack instead of asking humans or LLMs to inspect raw telemetry directly.

---

## 3. Why This Is Not Just Another AI SRE Chatbot

Many AI observability projects focus on:

- Root-cause prediction.
- Chat with logs.
- Incident summary generation.
- Auto-remediation.
- Alert triage.

FCAPSule AI focuses on a different layer:

> **Telemetry attention and evidence selection.**

The system is valuable even when root cause is unknown or when the observability team does not receive feedback about how an application team fixed the issue.

Instead of claiming:

> "The root cause is definitely X."

FCAPSule AI produces:

> "Here is the strongest evidence collected from logs, metrics, faults and infrastructure context. Here is what was included, what was excluded, and why."

This is easier to evaluate, safer to deploy, and better aligned with an observability framework team.

---

## 4. University Template Alignment

The selected template is:

> **CM3020 Artificial Intelligence - Project Idea 4.1: Orchestrating AI models to achieve a goal**

The university template expects a working software system that combines multiple pre-trained models into a workflow to achieve a clear goal. It specifically expects at least three pre-trained models, ideally operating on different domains or data spaces.

### 4.1 Clear goal

**Goal:** Generate compact, useful incident evidence capsules from large-scale multimodal telemetry.

The project goal is specific and testable.

Input:

- Incident time window or alert trigger.
- Application/service/namespace/cluster.
- Logs, metrics, faults, infrastructure context.

Output:

- Evidence capsule with ranked evidence, timeline, anomalous metrics, key log templates, fault sequence, telemetry health signals, and next investigation steps.

### 4.2 Multiple pre-trained models

Use at least three models from different domains. Recommended minimum:

| Model | Domain | Purpose |
|---|---|---|
| Log parsing / embedding model | Text/logs | Cluster logs into templates, detect similarity, select representative snippets |
| Time-series anomaly model | Metrics | Detect abnormal Prometheus metric windows |
| LLM reasoning model | Natural language / orchestration | Rank evidence, explain findings, generate capsule report |

Strong version:

| Model | Domain | Purpose |
|---|---|---|
| Log template extraction model or algorithm + embedding model | Logs/text | Reduce millions of raw logs into templates and clusters |
| Sentence embedding model | Text semantics | Similarity search and retrieval over log patterns / past capsules |
| Time-series anomaly detector | Metrics | Detect metric windows worth including |
| LLM planner/reasoner | Language | Coordinate evidence, generate explanations, rank usefulness |
| Optional VLM | Visual/dashboard | Interpret Grafana/OpenSearch screenshots for demo multimodality |
| Optional graph/topology model | Infrastructure relationships | Connect app -> service -> namespace -> pod -> cluster -> fault/metric/log evidence |

### 4.3 Different data spaces/domains

FCAPSule AI uses multiple telemetry domains:

- **Text logs** from Kafka/OpenSearch.
- **Time-series metrics** from Prometheus.
- **Fault/event streams** from alert/fault systems.
- **Infrastructure metadata** from Kubernetes/app registry/config.
- **Optional visual evidence** from dashboard screenshots.

This exceeds the minimum "three domains" requirement.

### 4.4 Evidence of testing and rejecting models

The report should include experiments comparing multiple model combinations:

- Log parser A vs log parser B.
- Embedding model A vs embedding model B.
- Time-series anomaly model A vs statistical baseline.
- DeepSeek vs Qwen vs local Llama vs other LLM for evidence ranking.
- Single LLM over sampled logs vs orchestrated multi-model pipeline.

Record:

- Accuracy / usefulness.
- Cost.
- Latency.
- Token usage.
- Hallucination / unsupported claim rate.
- Human usefulness score.

### 4.5 Working integrated software

The final product should be a working CLI, API or small web app.

Minimum acceptable final demo:

```bash
fcapsule investigate \
  --app checkout-service \
  --cluster prod-cluster-a \
  --from 2026-05-01T10:00:00Z \
  --to 2026-05-01T11:00:00Z
```

Expected output:

```text
FCAPSule Evidence Capsule
- Summary
- Timeline
- Top faults
- Top anomalous metrics
- Top log templates
- Telemetry delay notes
- Evidence ranking
- Suggested next steps
- Confidence / limitations
```

### 4.6 Evaluation

Evaluation must be explicit. Suggested metrics:

| Metric | Meaning |
|---|---|
| Compression ratio | Raw telemetry size vs evidence capsule size |
| Signal preservation | Whether key fault/anomaly windows remain represented |
| Evidence coverage | Whether logs + metrics + faults are all represented |
| Human usefulness score | Expert review from observability engineers |
| Token reduction | How many LLM tokens saved compared with raw/sampled logs |
| Latency | Time to generate capsule |
| Cost | Estimated model/API cost |
| Hallucination rate | Unsupported claims in generated summaries |
| Faithfulness | Every conclusion must cite evidence source IDs |
| Model comparison | Single-model baseline vs multi-model orchestration |

### 4.7 Integrated evaluation scope

The integrated system should demonstrate models and deterministic methods operating successfully toward the overall goal.

Evaluation target:

- Use a small dataset or anonymized/synthetic data.
- Process logs, metrics, and faults for one incident window.
- Generate one evidence capsule.
- Show that each model contributes a different type of evidence.

### 4.8 Outstanding project behavior

To aim for top marks:

- Use at least four data domains.
- Compare several model choices.
- Evaluate against baselines.
- Include human review.
- Provide clear software tests.
- Show iterative development.
- Explain limitations honestly.
- Produce a polished demo and report.
- Make the architecture extensible with adapters.

---

## 5. Main System Output: Incident Evidence Capsule

The evidence capsule is the core artifact.

It should exist in two forms:

- **JSON capsule:** structured output for testing, evaluation, storage and future automation.
- **Markdown report:** human-readable output for engineers, academic report screenshots and demo presentation.

Conceptually, a capsule should contain:

- Incident metadata: app, cluster, namespace, time window.
- Short summary of the event window.
- Timeline of important events.
- Top faults.
- Top anomalous metrics.
- Top log patterns.
- Telemetry health notes, such as log indexing delay or Prometheus scrape gaps.
- Evidence ranking.
- Evidence excluded from the capsule and why.
- Next investigation steps.
- Limitations and confidence notes.

Example human-readable capsule:

```markdown
# FCAPSule Evidence Capsule: checkout-service / prod-a

## Summary
Between 10:03 and 10:32 UTC, checkout-service showed elevated error rate, timeout logs, and critical faults.

## Strongest Evidence
1. Error rate increased 8x above baseline.
2. Timeout log template increased 12.4x.
3. Critical fault began one minute after metric anomaly.
4. OpenSearch delay increased, so evidence completeness is reduced.

## Next Investigation Steps
- Inspect affected pods.
- Check upstream dependency latency.
- Verify whether logs after 10:30 arrived late.
```

The exact schema and fields are implementation details and may change. The stable requirement is that the capsule must be compact, evidence-grounded, explainable and useful for investigation.

---

## 6. Development Scope

### 6.1 MVP scope

The MVP must be small but complete.

MVP features:

1. Ingest a fixed incident window.
2. Load logs from CSV/JSON/OpenSearch export.
3. Load metrics from Prometheus query export or CSV.
4. Load faults from CSV/JSON.
5. Extract log templates and top patterns.
6. Detect metric anomalies.
7. Build a fault timeline.
8. Rank evidence using an LLM.
9. Generate Markdown + JSON evidence capsule.
10. Evaluate compression ratio and signal preservation.

### 6.2 Strong final scope

Add:

1. Adapters for OpenSearch, Prometheus, and fault JSON.
2. Kafka/OpenSearch delay analysis.
3. Kubernetes metadata mapping.
4. Model comparison runner.
5. Human evaluation form.
6. Dashboard or Streamlit UI.
7. Evidence citations inside generated capsule.
8. Optional Grafana screenshot interpretation.
9. Optional vector store for previous capsules.
10. Optional synthetic demo dataset for public GitHub.

### 6.3 Out of scope

Do not attempt:

- Full auto-remediation.
- Guaranteed root-cause prediction.
- Direct production changes.
- Reading every raw log with an LLM.
- Training a large model from scratch.
- Building a full observability vendor platform.

### 6.4 Stretch goals

If time remains:

- Capsule similarity search: "find past evidence capsules similar to this one."
- Capsule diff: compare two incident windows.
- Agent mode: multiple agents debate evidence inclusion.
- Grafana panel generator for the capsule.
- Automatic postmortem draft.
- OpenTelemetry Collector policy recommendation.

---

## 7. Evaluation Plan

### 7.1 Baselines

Compare FCAPSule AI against:

1. **Raw sample baseline:** randomly sample logs and ask LLM to summarize.
2. **Top-volume baseline:** choose highest-volume log patterns and top alerts.
3. **Single-LLM baseline:** feed a limited raw telemetry sample to one LLM.
4. **FCAPSule pipeline:** log templates + metric anomalies + faults + evidence ranking.

### 7.2 Metrics

**Compression ratio**

```text
compression_ratio = raw_input_size / capsule_size
```

Examples:

- 2,000,000 logs -> 30 log patterns.
- 500 MB raw logs -> 15 KB capsule.

**Signal preservation**

Define known signal windows:

- Fault windows.
- Metric anomaly windows.
- Critical log severity windows.
- Kafka/OpenSearch delay windows.
- Pod restart windows.

Measure how many appear in the capsule.

**Evidence diversity**

Check whether the capsule includes at least:

- One log evidence item.
- One metric evidence item.
- One fault evidence item.
- One telemetry health/context item.

**Faithfulness**

Manual or automated check:

- Every major claim must cite evidence IDs.
- Penalize unsupported statements.

**Human usefulness**

Ask reviewers to rate 1-5:

- Clarity.
- Completeness.
- Usefulness for investigation.
- Trustworthiness.
- Missing important evidence.

**Token and cost reduction**

Estimate:

- Tokens required for raw/sampled telemetry.
- Tokens required for capsule generation.
- API cost per investigation.

**Latency**

Measure:

- Adapter loading time.
- Model processing time.
- LLM generation time.
- Total capsule generation time.

### 7.3 Expected result format

```markdown
## Evaluation Summary

Dataset: 10 incident windows across 5 applications

| Method | Compression | Signal Preservation | Human Usefulness | Avg Tokens | Avg Latency |
|---|---:|---:|---:|---:|---:|
| Random sample + LLM | 20x | 52% | 2.8/5 | 18k | 35s |
| Top-volume baseline | 35x | 61% | 3.1/5 | 12k | 22s |
| FCAPSule AI | 120x | 88% | 4.2/5 | 4k | 28s |
```

The exact numbers above are illustrative. Do not include fake results in the final report; replace them with measured results.

---

## 8. Security and Privacy Principles

Because production telemetry may contain sensitive information:

- Never commit real company data.
- Use anonymized samples.
- Redact secrets, tokens, phone numbers, customer identifiers, IPs if needed.
- Keep adapters configurable but use synthetic data in public repo.
- Store only evidence IDs or redacted snippets in generated capsules.
- Avoid sending sensitive raw logs to external APIs unless approved.
- Prefer local models for sensitive experiments when possible.

Add a privacy section to the report.

---

## 9. Literature Review Notes

Use these as starting references.

### 9.1 RCACopilot

**Paper:** *Automatic Root Cause Analysis via Large Language Models for Cloud Incidents*

**URL:** https://arxiv.org/abs/2305.15778

Relevance:

- Shows that LLMs can help with root cause analysis for cloud incidents.
- Evaluated on real Microsoft incident data.
- Demonstrates value of aggregating diagnostic information.

Gap:

- Focuses on root cause prediction.
- FCAPSule AI focuses on evidence selection before root cause reasoning.

### 9.2 AIOpsLab

**Paper:** *AIOpsLab: A Holistic Framework to Evaluate AI Agents for Enabling Autonomous Clouds*

**URL:** https://arxiv.org/abs/2501.06706

Relevance:

- Shows the direction of AIOps research toward autonomous agents and evaluation frameworks.
- Uses microservice environments, fault injection, telemetry export, and agent interfaces.

Gap:

- Focuses on controlled benchmark environments.
- FCAPSule AI focuses on evidence capsules from large-scale telemetry where reproduction may not be possible.

### 9.3 LLM4Log

**Paper:** *LLM4Log: A Systematic Review of Large Language Model-based Log Analysis*

**URL:** https://arxiv.org/abs/2604.16359

Relevance:

- Surveys LLM usage across log parsing, anomaly detection, failure prediction, RCA, summarization.
- Highlights real deployment challenges: context limits, latency, cost, privacy, hallucinations, drift, grounding.

Gap:

- FCAPSule AI directly addresses the context/grounding bottleneck by selecting compact evidence before LLM reasoning.

### 9.4 LogCleaner

**Paper:** *Reducing Events to Augment Log-based Anomaly Detection Models: An Empirical Study*

**URL:** https://arxiv.org/abs/2409.04834

Relevance:

- Demonstrates that reducing noisy/redundant log events can improve anomaly detection efficiency.
- Provides support for telemetry reduction and signal preservation as a valid research direction.

Gap:

- Focuses mainly on logs and anomaly detection.
- FCAPSule AI extends the idea to logs + metrics + faults + telemetry health.

### 9.5 AdaptiveLog

**Paper:** *AdaptiveLog: An Adaptive Log Analysis Framework with the Collaboration of Large and Small Language Model*

**URL:** https://arxiv.org/abs/2501.11031

Relevance:

- Shows cost-aware collaboration between smaller and larger language models.
- Useful pattern for FCAPSule AI: cheap models filter, expensive models explain.

Gap:

- Primarily log-analysis focused.
- FCAPSule AI uses multimodal telemetry attention.

### 9.6 FCAPS background

**Topic:** FCAPS network management model

**Reference starting point:** https://en.wikipedia.org/wiki/FCAPS

Relevance:

- Provides conceptual grounding for Fault, Configuration, Accounting/Administration, Performance, Security.
- The project name and domain framing come from this model.

---

## 10. Presentation / Marketing Narrative

### 10.1 University pitch

> FCAPSule AI is a system that orchestrates pre-trained AI models across logs, metrics, fault alerts, and infrastructure metadata to generate compact incident evidence capsules. The project investigates whether multi-model telemetry attention can reduce the amount of data engineers need to inspect while preserving important diagnostic signal.

### 10.2 LinkedIn pitch

> I built FCAPSule AI - a flight recorder for cloud incidents.
> It uses multiple AI models to compress logs, Prometheus metrics, fault alerts and infrastructure context into a compact evidence capsule.
> The goal is simple: help humans and AI agents stop drowning in telemetry.

### 10.3 Manager pitch

> FCAPSule AI can reduce the time engineers spend collecting and organizing observability evidence. Instead of manually checking logs, metrics, faults and telemetry delays across different systems, it produces a structured investigation package that can be shared with application teams.

### 10.4 Report thesis

> The main bottleneck in AI-assisted operations is not only reasoning over telemetry, but selecting trustworthy evidence from massive, noisy, multimodal telemetry streams.

---

## 11. Success Criteria

The project is successful if:

- It processes at least three telemetry domains.
- It uses at least three pre-trained models or model-like components.
- It generates useful JSON and Markdown evidence capsules.
- It reduces raw telemetry into a compact evidence package.
- It preserves important fault/anomaly signals.
- It cites evidence IDs for generated claims.
- It compares against baselines.
- It includes a clear literature review.
- It includes a clear evaluation methodology.
- It is explainable enough for a 3-5 minute video pitch.

---

## 12. Final Memory Aid

Do not forget the core thesis:

> **FCAPSule AI is not another AI SRE chatbot. It is an AI evidence selection system for cloud incidents.**

Do not overclaim:

- It does not guarantee root cause.
- It does not auto-fix production.
- It does not replace engineers.

Do claim:

- It compresses telemetry.
- It preserves important signals.
- It makes evidence usable.
- It orchestrates multiple AI models.
- It is measurable.
- It is aligned with FCAPS/AIOps/observability.
