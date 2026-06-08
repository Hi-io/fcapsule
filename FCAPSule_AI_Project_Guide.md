# FCAPSule AI — Project Development Guide

**Project subtitle:** A Multimodal Telemetry Attention Engine for Cloud Incident Evidence  
**Public tagline:** *A flight recorder for cloud incidents.*  
**Academic framing:** Orchestrating pre-trained AI models to generate compact, investigation-ready evidence capsules from FCAPS-style telemetry.

---

## 0. Executive Summary

FCAPSule AI is an AI orchestration project designed to solve a specific problem in modern observability: when large-scale cloud systems degrade, they generate too much telemetry for humans or AI agents to inspect directly. Logs, metrics, fault alerts, Kafka delays, OpenSearch indexing delays, Kubernetes metadata, and dashboards all contain useful evidence, but the useful signal is buried inside massive noisy streams.

The project does **not** try to be another generic AI SRE chatbot or a full root-cause analysis platform. Instead, it focuses on the earlier and more fundamental bottleneck:

> **Before humans or AI agents can reason about an incident, someone has to select the right evidence.**

FCAPSule AI generates an **Incident Evidence Capsule**: a compact, explainable package containing the most relevant log patterns, anomalous metrics, fault timeline, affected infrastructure context, telemetry delay indicators, and suggested next investigation steps.

The project is inspired by the FCAPS network management model: Fault, Configuration, Accounting/Administration, Performance, and Security. In the first implementation, the practical focus is mainly on:

- **Fault**: alerts, faults, severity, fault timelines.
- **Performance**: Prometheus metrics, metric anomalies, resource saturation, latency/error signals.
- **Logs**: OpenSearch/Kafka logs, log templates, log volume, log delays, repeated patterns.
- **Configuration / Context**: Kubernetes metadata, app name, namespace, cluster, pod, service, stage, owners if available.

The final system should demonstrate orchestration of multiple pre-trained models across different data domains, as required by the University of London CM3020 Artificial Intelligence template 4.1: **Orchestrating AI models to achieve a goal**.

---

## 1. Core Project Idea

### 1.1 One-sentence version

**FCAPSule AI uses multiple AI models to compress noisy production telemetry into compact evidence capsules for cloud incident investigation.**

### 1.2 Product-style explanation

When production gets noisy, engineers often face millions of logs, hundreds of metrics, and many fault alerts. FCAPSule AI acts like a flight recorder for cloud incidents: it captures the important evidence around an event and packages it into a small, readable, AI-grounded investigation capsule.

### 1.3 Academic research question

> **Can orchestrated pre-trained AI models select and summarize useful multimodal telemetry evidence while preserving diagnostic signal and reducing investigation context size?**

Alternative research question:

> **How can multi-model AI orchestration transform large-scale FCAPS telemetry into compact evidence capsules that are useful for human and AI-assisted incident investigation?**

### 1.4 Problem being solved

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

## 2. Why This Is Not Just Another AI SRE Chatbot

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

> “The root cause is definitely X.”

FCAPSule AI produces:

> “Here is the strongest evidence collected from logs, metrics, faults and infrastructure context. Here is what was included, what was excluded, and why.”

This is easier to evaluate, safer to deploy, and better aligned with an observability framework team.

---

## 3. University Template Alignment — 101% Checklist

The selected template is:

> **CM3020 Artificial Intelligence — Project Idea 4.1: Orchestrating AI models to achieve a goal**

The university template expects a working software system that combines multiple pre-trained models into a workflow to achieve a clear goal. It specifically expects at least three pre-trained models, ideally operating on different domains or data spaces.

### 3.1 Template requirement: Clear goal

**Goal:** Generate compact, useful incident evidence capsules from large-scale multimodal telemetry.

The project goal is specific and testable:

Input:

- Incident time window or alert trigger.
- Application/service/namespace/cluster.
- Logs, metrics, faults, infrastructure context.

Output:

- Evidence capsule with ranked evidence, timeline, anomalous metrics, key log templates, fault sequence, telemetry health signals, and next investigation steps.

### 3.2 Template requirement: Multiple pre-trained models

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
| Optional graph/topology model | Infrastructure relationships | Connect app → service → namespace → pod → cluster → fault/metric/log evidence |

### 3.3 Template requirement: Different data spaces/domains

FCAPSule AI uses multiple telemetry domains:

- **Text logs** from Kafka/OpenSearch.
- **Time-series metrics** from Prometheus.
- **Fault/event streams** from alert/fault systems.
- **Infrastructure metadata** from Kubernetes/app registry/config.
- **Optional visual evidence** from dashboard screenshots.

This exceeds the minimum “three domains” requirement.

### 3.4 Template requirement: Evidence of testing and rejecting models

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

### 3.5 Template requirement: Working integrated software

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

### 3.6 Template requirement: Evaluation

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

### 3.7 Template requirement: Prototype scope

Prototype should demonstrate models operating successfully and being combined toward the overall goal.

Prototype target:

- Use a small dataset or anonymized/synthetic data.
- Process logs, metrics, and faults for one incident window.
- Generate one evidence capsule.
- Show that each model contributes a different type of evidence.

### 3.8 Template requirement: Outstanding project behavior

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

## 4. Main System Output — Incident Evidence Capsule

The evidence capsule is the core artifact.

### 4.1 Capsule structure

Suggested JSON schema:

```json
{
  "capsule_id": "fcapsule-2026-05-01-checkout-service-prod-a",
  "app": "checkout-service",
  "cluster": "prod-a",
  "namespace": "checkout",
  "time_window": {
    "from": "2026-05-01T10:00:00Z",
    "to": "2026-05-01T11:00:00Z"
  },
  "summary": "Short human-readable summary of the event window.",
  "timeline": [
    {
      "timestamp": "2026-05-01T10:04:00Z",
      "type": "fault",
      "description": "High error rate fault triggered",
      "source": "fault-system",
      "evidence_id": "fault-001"
    }
  ],
  "faults": [
    {
      "fault_id": "fault-001",
      "name": "HighErrorRate",
      "severity": "critical",
      "first_seen": "2026-05-01T10:04:00Z",
      "last_seen": "2026-05-01T10:32:00Z",
      "affected_entities": ["checkout-service", "pod-abc"]
    }
  ],
  "metric_anomalies": [
    {
      "metric": "http_requests_error_rate",
      "score": 0.94,
      "window": "10:03-10:30",
      "description": "Error rate increased 8x above baseline",
      "evidence_id": "metric-001"
    }
  ],
  "log_patterns": [
    {
      "template": "Request failed with timeout after <*> ms",
      "count": 184220,
      "change_vs_baseline": "12.4x",
      "severity": "ERROR",
      "representative_examples": ["..."],
      "usefulness_score": 0.91,
      "evidence_id": "log-001"
    }
  ],
  "telemetry_health": {
    "log_indexing_delay_p95_seconds": 430,
    "kafka_lag_status": "elevated",
    "prometheus_scrape_gaps": 2,
    "notes": [
      "OpenSearch delay increased during the same window, so late-arriving logs may be missing from the first capsule."
    ]
  },
  "evidence_ranking": [
    {
      "rank": 1,
      "evidence_id": "metric-001",
      "reason": "Metric anomaly aligns with first critical fault."
    }
  ],
  "excluded_evidence": [
    {
      "source": "logs",
      "reason": "Repeated heartbeat logs had high volume but low correlation with faults or anomalies."
    }
  ],
  "next_steps": [
    "Check pods with repeated timeout log pattern.",
    "Verify OpenSearch indexing delay before assuming logs are complete.",
    "Compare error-rate anomaly with recent deployment/config changes if available."
  ],
  "limitations": [
    "This capsule does not claim final root cause.",
    "Some logs may be delayed due to indexing lag."
  ]
}
```

### 4.2 Human-readable capsule report

The system should also generate Markdown:

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

---

## 5. Development Scope

### 5.1 MVP scope

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

### 5.2 Strong final scope

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

### 5.3 Out of scope

Do not attempt:

- Full auto-remediation.
- Guaranteed root-cause prediction.
- Direct production changes.
- Reading every raw log with an LLM.
- Training a large model from scratch.
- Building a full observability vendor platform.

### 5.4 Stretch goals

If time remains:

- Capsule similarity search: “find past evidence capsules similar to this one.”
- Capsule diff: compare two incident windows.
- Agent mode: multiple agents debate evidence inclusion.
- Grafana panel generator for the capsule.
- Automatic postmortem draft.
- OpenTelemetry Collector policy recommendation.

---

## 6. Proposed Architecture

```text
                ┌─────────────────────────────┐
                │ Incident Trigger             │
                │ app / cluster / time window  │
                └──────────────┬──────────────┘
                               ↓
┌────────────────────────────────────────────────────────────┐
│ Data Adapters                                               │
│ - OpenSearch logs                                           │
│ - Prometheus metrics                                        │
│ - Fault/alert stream                                        │
│ - Kafka/OpenSearch delay metadata                           │
│ - Kubernetes metadata                                       │
└──────────────────────┬─────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────────┐
│ Preprocessing Layer                                         │
│ - normalize timestamps                                      │
│ - map app/service/pod/cluster                               │
│ - deduplicate events                                        │
│ - compute baseline windows                                  │
└──────────────────────┬─────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────────┐
│ Model Layer                                                 │
│ - Log Template Model                                        │
│ - Metric Anomaly Model                                      │
│ - Fault Timeline Classifier                                 │
│ - Embedding Retrieval                                       │
│ - LLM Evidence Ranker / Explainer                           │
└──────────────────────┬─────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────────┐
│ Evidence Attention Layer                                    │
│ - score evidence                                            │
│ - select evidence                                           │
│ - explain inclusion/exclusion                               │
│ - cite source IDs                                           │
└──────────────────────┬─────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────────┐
│ Capsule Generator                                           │
│ - JSON capsule                                              │
│ - Markdown report                                           │
│ - optional UI                                               │
└────────────────────────────────────────────────────────────┘
```

---

## 7. Recommended Tech Stack

### 7.1 Language and backend

- Python 3.11+
- FastAPI for API mode
- Typer or Click for CLI mode
- Pydantic for schemas
- Pandas / Polars for data processing
- NumPy / SciPy / scikit-learn for metrics/anomaly utilities

### 7.2 Data sources

- OpenSearch client for logs
- Prometheus HTTP API for metrics
- CSV/JSON fallback loaders for development
- Kafka metadata from exported topic/consumer data if direct access is not safe
- Kubernetes metadata via exported JSON or API if available

### 7.3 Models

Possible choices:

**Log processing**

- Drain3-style log template parser.
- SentenceTransformers / BGE / E5 embeddings for semantic grouping.
- Optional LLM for semantic classification of log patterns.

**Metrics**

- Statistical baseline: z-score, rolling median/MAD.
- Isolation Forest for anomaly detection.
- Prophet / Chronos / TimesFM / other time-series model if feasible.

**LLM reasoning**

- DeepSeek, Qwen, GPT, Claude, Llama, or local model.
- Must compare at least two if possible.

**Optional VLM**

- Qwen-VL, LLaVA, GPT vision, or similar for screenshots.

### 7.4 Storage

- Local filesystem for MVP.
- SQLite/Postgres for metadata.
- Qdrant/Chroma for vector retrieval if implementing capsule memory.

### 7.5 UI

- CLI first.
- Streamlit for fast demo.
- Optional React/FastAPI later.

---

## 8. Repository Structure

```text
fcapsule-ai/
├── README.md
├── pyproject.toml
├── .env.example
├── configs/
│   ├── sample_app.yaml
│   ├── model_config.yaml
│   └── scoring_config.yaml
├── data/
│   ├── sample/
│   │   ├── logs.jsonl
│   │   ├── metrics.csv
│   │   ├── faults.json
│   │   └── k8s_metadata.json
│   └── README.md
├── fcapsule/
│   ├── __init__.py
│   ├── cli.py
│   ├── api.py
│   ├── schemas/
│   │   ├── capsule.py
│   │   ├── telemetry.py
│   │   └── evidence.py
│   ├── adapters/
│   │   ├── base.py
│   │   ├── opensearch_adapter.py
│   │   ├── prometheus_adapter.py
│   │   ├── fault_adapter.py
│   │   ├── kafka_adapter.py
│   │   └── k8s_adapter.py
│   ├── preprocessors/
│   │   ├── timestamps.py
│   │   ├── normalizer.py
│   │   └── entity_mapper.py
│   ├── models/
│   │   ├── log_template_extractor.py
│   │   ├── metric_anomaly_detector.py
│   │   ├── fault_timeline_builder.py
│   │   ├── embedding_retriever.py
│   │   └── llm_evidence_ranker.py
│   ├── attention/
│   │   ├── evidence_scoring.py
│   │   ├── selector.py
│   │   └── exclusion_explainer.py
│   ├── generation/
│   │   ├── capsule_json.py
│   │   ├── capsule_markdown.py
│   │   └── prompt_templates.py
│   ├── evaluation/
│   │   ├── compression.py
│   │   ├── signal_preservation.py
│   │   ├── hallucination_check.py
│   │   ├── human_eval.py
│   │   └── model_comparison.py
│   └── utils/
│       ├── logging.py
│       └── time_windows.py
├── tests/
│   ├── test_log_templates.py
│   ├── test_metric_anomalies.py
│   ├── test_capsule_generation.py
│   └── test_evaluation.py
├── notebooks/
│   ├── exploratory_log_analysis.ipynb
│   └── evaluation_results.ipynb
├── reports/
│   ├── sample_capsule.md
│   └── evaluation_summary.md
└── docs/
    ├── architecture.md
    ├── literature_review_notes.md
    └── university_template_checklist.md
```

---

## 9. Data Model

### 9.1 Log event

```python
class LogEvent(BaseModel):
    timestamp: datetime
    indexed_at: datetime | None = None
    app: str | None = None
    cluster: str | None = None
    namespace: str | None = None
    pod: str | None = None
    container: str | None = None
    severity: str | None = None
    message: str
    raw: dict = {}
```

### 9.2 Metric point

```python
class MetricPoint(BaseModel):
    timestamp: datetime
    metric_name: str
    value: float
    labels: dict[str, str] = {}
```

### 9.3 Fault event

```python
class FaultEvent(BaseModel):
    timestamp: datetime
    fault_name: str
    severity: str
    app: str | None = None
    cluster: str | None = None
    namespace: str | None = None
    pod: str | None = None
    description: str | None = None
    raw: dict = {}
```

### 9.4 Evidence item

```python
class EvidenceItem(BaseModel):
    evidence_id: str
    evidence_type: Literal["log", "metric", "fault", "topology", "telemetry_health"]
    source: str
    title: str
    description: str
    timestamp_range: tuple[datetime, datetime] | None = None
    score: float
    confidence: float
    raw_refs: list[str]
    reason_for_inclusion: str
```

---

## 10. Evidence Scoring

A simple scoring function is enough for MVP. It must be explainable.

### 10.1 Log pattern usefulness score

```text
log_usefulness =
  severity_weight
+ anomaly_window_overlap
+ fault_window_overlap
+ rarity_score
+ change_vs_baseline
+ semantic_debug_value
- repetition_penalty
- known_noise_penalty
```

### 10.2 Metric usefulness score

```text
metric_usefulness =
  anomaly_score
+ service_relevance
+ fault_temporal_overlap
+ known_sli_weight
+ change_vs_baseline
```

### 10.3 Fault usefulness score

```text
fault_usefulness =
  severity_weight
+ first_occurrence_weight
+ affected_entity_count
+ duration_weight
+ correlation_with_logs_metrics
```

### 10.4 Telemetry health score

Track if evidence might be incomplete:

- Log generated_at vs indexed_at delay.
- Kafka lag / topic growth.
- Missing logs from pods.
- Prometheus scrape gaps.
- Fault timestamp ordering issues.

This is important because the capsule should not pretend evidence is complete when the pipeline itself was delayed.

---

## 11. LLM Prompting Strategy

### 11.1 Key principle

The LLM should not inspect raw logs directly except for small representative examples. It should receive structured evidence candidates from other models.

### 11.2 Evidence ranking prompt

```text
You are an observability evidence ranking assistant.
Your task is NOT to determine a final root cause.
Your task is to rank evidence items by usefulness for incident investigation.

Input:
- incident metadata
- candidate log patterns
- candidate metric anomalies
- fault timeline
- telemetry health notes

Rules:
1. Every claim must reference evidence IDs.
2. Do not invent missing data.
3. If telemetry is delayed or incomplete, mention it as a limitation.
4. Prefer evidence that overlaps across multiple domains.
5. Explain why high-volume evidence may be excluded if it is repetitive or weakly correlated.

Output:
- ranked evidence list
- capsule summary
- excluded evidence explanation
- next investigation steps
- limitations
```

### 11.3 Capsule summary prompt

```text
Generate a concise incident evidence capsule for an engineer.
Do not claim final root cause.
Use only the supplied evidence items.
Cite evidence IDs after each important statement.
Separate strong evidence from weak evidence.
Include telemetry completeness warnings.
```

---

## 12. Evaluation Plan

### 12.1 Baselines

Compare FCAPSule AI against:

1. **Raw sample baseline**: randomly sample logs and ask LLM to summarize.
2. **Top-volume baseline**: choose highest-volume log patterns and top alerts.
3. **Single-LLM baseline**: feed a limited raw telemetry sample to one LLM.
4. **FCAPSule pipeline**: log templates + metric anomalies + faults + evidence ranking.

### 12.2 Metrics

#### Compression ratio

```text
compression_ratio = raw_input_size / capsule_size
```

Examples:

- 2,000,000 logs → 30 log patterns.
- 500 MB raw logs → 15 KB capsule.

#### Signal preservation

Define known signal windows:

- Fault windows.
- Metric anomaly windows.
- Critical log severity windows.
- Kafka/OpenSearch delay windows.
- Pod restart windows.

Measure how many appear in the capsule.

#### Evidence diversity

Check whether the capsule includes at least:

- One log evidence item.
- One metric evidence item.
- One fault evidence item.
- One telemetry health/context item.

#### Faithfulness

Manual or automated check:

- Every major claim must cite evidence IDs.
- Penalize unsupported statements.

#### Human usefulness

Ask reviewers to rate 1–5:

- Clarity.
- Completeness.
- Usefulness for investigation.
- Trustworthiness.
- Missing important evidence.

#### Token and cost reduction

Estimate:

- Tokens required for raw/sampled telemetry.
- Tokens required for capsule generation.
- API cost per investigation.

#### Latency

Measure:

- Adapter loading time.
- Model processing time.
- LLM generation time.
- Total capsule generation time.

### 12.3 Expected result format

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

## 13. Development Milestones

### Week 1–2: Project foundation

- Create repo and architecture docs.
- Build sample data format.
- Implement schemas.
- Create CLI skeleton.
- Implement local JSON/CSV loaders.
- Generate first empty capsule template.

Deliverable:

```bash
fcapsule investigate --sample data/sample
```

### Week 3–4: Log pipeline

- Implement log normalization.
- Implement log template extraction.
- Compute top templates by volume.
- Compute severity counts.
- Compute change vs baseline if baseline window exists.
- Add representative examples.

Deliverable:

- Log evidence section in capsule.

### Week 5–6: Metrics pipeline

- Implement Prometheus export loader.
- Implement anomaly detection baseline.
- Rank metric anomalies.
- Add metric evidence section.

Deliverable:

- Metric evidence section in capsule.

### Week 7–8: Fault timeline pipeline

- Implement fault loader.
- Build timeline.
- Rank faults by severity/time/entity.
- Correlate faults with log/metric windows.

Deliverable:

- Fault timeline in capsule.

### Week 9–10: LLM evidence ranking

- Implement model abstraction.
- Add DeepSeek/Qwen/local/GPT-compatible providers.
- Build prompt templates.
- Generate capsule summary and next steps.
- Enforce evidence citations.

Deliverable:

- Full Markdown and JSON capsule.

### Week 11–12: Evaluation framework

- Implement compression metrics.
- Implement signal preservation metrics.
- Implement human evaluation form.
- Compare baselines.

Deliverable:

- Evaluation report.

### Week 13–14: Polish and demo

- Add Streamlit UI or polished CLI output.
- Add anonymized sample dataset.
- Add diagrams.
- Add README.
- Prepare final video and report figures.

---

## 14. API / CLI Design

### 14.1 CLI commands

```bash
fcapsule investigate --config configs/sample_app.yaml
```

```bash
fcapsule evaluate --dataset data/eval --methods random_sample top_volume fcapsule
```

```bash
fcapsule compare-models --config configs/model_config.yaml
```

```bash
fcapsule render --capsule reports/capsule.json --format markdown
```

### 14.2 API endpoints

```http
POST /investigate
GET /capsules/{capsule_id}
POST /evaluate
GET /health
```

### 14.3 Config file example

```yaml
app: checkout-service
cluster: prod-a
namespace: checkout
window:
  from: "2026-05-01T10:00:00Z"
  to: "2026-05-01T11:00:00Z"
data_sources:
  logs:
    type: jsonl
    path: data/sample/logs.jsonl
  metrics:
    type: csv
    path: data/sample/metrics.csv
  faults:
    type: json
    path: data/sample/faults.json
  k8s:
    type: json
    path: data/sample/k8s_metadata.json
models:
  log_embeddings: sentence-transformers/all-MiniLM-L6-v2
  llm_provider: deepseek
  metric_anomaly: rolling_mad
output:
  json: reports/sample_capsule.json
  markdown: reports/sample_capsule.md
```

---

## 15. Security and Privacy Considerations

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

## 16. Literature Review Notes

Use these as starting references.

### 16.1 RCACopilot

**Paper:** *Automatic Root Cause Analysis via Large Language Models for Cloud Incidents*  
**URL:** https://arxiv.org/abs/2305.15778

Relevance:

- Shows that LLMs can help with root cause analysis for cloud incidents.
- Evaluated on real Microsoft incident data.
- Demonstrates value of aggregating diagnostic information.

Gap:

- Focuses on root cause prediction.
- FCAPSule AI focuses on evidence selection before root cause reasoning.

### 16.2 AIOpsLab

**Paper:** *AIOpsLab: A Holistic Framework to Evaluate AI Agents for Enabling Autonomous Clouds*  
**URL:** https://arxiv.org/abs/2501.06706

Relevance:

- Shows the direction of AIOps research toward autonomous agents and evaluation frameworks.
- Uses microservice environments, fault injection, telemetry export, and agent interfaces.

Gap:

- Focuses on controlled benchmark environments.
- FCAPSule AI focuses on evidence capsules from large-scale telemetry where reproduction may not be possible.

### 16.3 LLM4Log

**Paper:** *LLM4Log: A Systematic Review of Large Language Model-based Log Analysis*  
**URL:** https://arxiv.org/abs/2604.16359

Relevance:

- Surveys LLM usage across log parsing, anomaly detection, failure prediction, RCA, summarization.
- Highlights real deployment challenges: context limits, latency, cost, privacy, hallucinations, drift, grounding.

Gap:

- FCAPSule AI directly addresses the context/grounding bottleneck by selecting compact evidence before LLM reasoning.

### 16.4 LogCleaner

**Paper:** *Reducing Events to Augment Log-based Anomaly Detection Models: An Empirical Study*  
**URL:** https://arxiv.org/abs/2409.04834

Relevance:

- Demonstrates that reducing noisy/redundant log events can improve anomaly detection efficiency.
- Provides support for telemetry reduction and signal preservation as a valid research direction.

Gap:

- Focuses mainly on logs and anomaly detection.
- FCAPSule AI extends the idea to logs + metrics + faults + telemetry health.

### 16.5 AdaptiveLog

**Paper:** *AdaptiveLog: An Adaptive Log Analysis Framework with the Collaboration of Large and Small Language Model*  
**URL:** https://arxiv.org/abs/2501.11031

Relevance:

- Shows cost-aware collaboration between smaller and larger language models.
- Useful pattern for FCAPSule AI: cheap models filter, expensive models explain.

Gap:

- Primarily log-analysis focused.
- FCAPSule AI uses multimodal telemetry attention.

### 16.6 FCAPS background

**Topic:** FCAPS network management model  
**Reference starting point:** https://en.wikipedia.org/wiki/FCAPS

Relevance:

- Provides conceptual grounding for Fault, Configuration, Accounting/Administration, Performance, Security.
- The project name and domain framing come from this model.

---

## 17. Presentation / Marketing Narrative

### 17.1 University pitch

> FCAPSule AI is a system that orchestrates pre-trained AI models across logs, metrics, fault alerts, and infrastructure metadata to generate compact incident evidence capsules. The project investigates whether multi-model telemetry attention can reduce the amount of data engineers need to inspect while preserving important diagnostic signal.

### 17.2 LinkedIn pitch

> I built FCAPSule AI — a flight recorder for cloud incidents.  
> It uses multiple AI models to compress logs, Prometheus metrics, fault alerts and infrastructure context into a compact evidence capsule.  
> The goal is simple: help humans and AI agents stop drowning in telemetry.

### 17.3 Manager pitch

> FCAPSule AI can reduce the time engineers spend collecting and organizing observability evidence. Instead of manually checking logs, metrics, faults and telemetry delays across different systems, it produces a structured investigation package that can be shared with application teams.

### 17.4 Report thesis

> The main bottleneck in AI-assisted operations is not only reasoning over telemetry, but selecting trustworthy evidence from massive, noisy, multimodal telemetry streams.

---

## 18. Success Criteria

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
- It is explainable enough for a 3–5 minute video pitch.

---

## 19. Codex / AI Coding Assistant Prompt

Use this as the starting prompt for Codex or another coding assistant:

```text
We are building FCAPSule AI, a Python project for a university final project.

Goal:
Create a multimodal telemetry attention engine that generates compact incident evidence capsules from logs, metrics, faults, and infrastructure metadata.

Core requirements:
1. Provide a CLI called `fcapsule`.
2. Load sample telemetry from local JSON/CSV/JSONL files.
3. Normalize logs, metrics, faults, and Kubernetes metadata into Pydantic models.
4. Extract log templates and rank top log patterns.
5. Detect metric anomalies using a simple baseline method first, such as rolling median/MAD or z-score.
6. Build a fault timeline from fault events.
7. Score and select evidence items across logs, metrics, faults, and telemetry health.
8. Use an LLM provider abstraction to generate a Markdown capsule summary from structured evidence.
9. Ensure generated claims cite evidence IDs.
10. Output both JSON and Markdown capsules.
11. Include evaluation utilities for compression ratio, signal preservation, token estimate, and human review template.
12. Include tests for core modules.

Do not build production integrations first. Start with local file adapters and clean architecture. Add OpenSearch/Prometheus adapters later behind interfaces.

Please create the initial repository structure, Pydantic schemas, CLI skeleton, sample data format, and a minimal working pipeline that reads local sample data and generates a basic evidence capsule.
```

---

## 20. Final Recommended MVP Definition

For the first working version, build exactly this:

1. `fcapsule investigate --config configs/sample_app.yaml`
2. Reads:
   - `logs.jsonl`
   - `metrics.csv`
   - `faults.json`
   - `k8s_metadata.json`
3. Produces:
   - `reports/sample_capsule.json`
   - `reports/sample_capsule.md`
4. Includes:
   - top 10 log templates;
   - top 10 metric anomalies;
   - fault timeline;
   - telemetry delay notes if available;
   - ranked evidence;
   - next investigation steps;
   - limitations;
   - compression metrics.
5. Compares:
   - random sample baseline;
   - top-volume baseline;
   - FCAPSule AI pipeline.

This is enough for a strong prototype and can be expanded safely.

---

## 21. Final Memory Aid

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

