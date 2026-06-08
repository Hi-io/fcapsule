# FCAPSule AI - Implementation Proposal

**Document status:** Draft implementation proposal. This file describes one possible way to build FCAPSule AI. Architecture, schemas, model choices, scoring functions, prompts, repository layout and API/CLI design are intentionally allowed to change as the project evolves.

For the stable project concept, goals, evaluation intent and academic framing, see `FCAPSule_AI_Concept.md`.

---

## 1. Working Assumption

The first implementation should be small, local-first and measurable. It should prove the concept before adding production integrations.

Recommended first target:

1. `fcapsule investigate --config configs/sample_app.yaml`
2. Read local sample files:
   - `logs.jsonl`
   - `metrics.csv`
   - `faults.json`
   - `k8s_metadata.json`
3. Produce:
   - `reports/sample_capsule.json`
   - `reports/sample_capsule.md`
4. Include:
   - top 10 log templates;
   - top 10 metric anomalies;
   - fault timeline;
   - telemetry delay notes if available;
   - ranked evidence;
   - next investigation steps;
   - limitations;
   - compression metrics.
5. Compare:
   - random sample baseline;
   - top-volume baseline;
   - FCAPSule AI pipeline.

This is enough for a strong prototype and can be expanded safely.

---

## 2. Proposed Architecture

```text
                +-----------------------------+
                | Incident Trigger             |
                | app / cluster / time window  |
                +--------------+--------------+
                               |
                               v
+------------------------------------------------------------+
| Data Adapters                                               |
| - OpenSearch logs                                           |
| - Prometheus metrics                                        |
| - Fault/alert stream                                        |
| - Kafka/OpenSearch delay metadata                           |
| - Kubernetes metadata                                       |
+----------------------+-------------------------------------+
                       |
                       v
+------------------------------------------------------------+
| Preprocessing Layer                                         |
| - normalize timestamps                                      |
| - map app/service/pod/cluster                               |
| - deduplicate events                                        |
| - compute baseline windows                                  |
+----------------------+-------------------------------------+
                       |
                       v
+------------------------------------------------------------+
| Model Layer                                                 |
| - Log Template Model                                        |
| - Metric Anomaly Model                                      |
| - Fault Timeline Classifier                                 |
| - Embedding Retrieval                                       |
| - LLM Evidence Ranker / Explainer                           |
+----------------------+-------------------------------------+
                       |
                       v
+------------------------------------------------------------+
| Evidence Attention Layer                                    |
| - score evidence                                            |
| - select evidence                                           |
| - explain inclusion/exclusion                               |
| - cite source IDs                                           |
+----------------------+-------------------------------------+
                       |
                       v
+------------------------------------------------------------+
| Capsule Generator                                           |
| - JSON capsule                                              |
| - Markdown report                                           |
| - optional UI                                               |
+------------------------------------------------------------+
```

---

## 3. Recommended Tech Stack

### 3.1 Language and backend

- Python 3.11+
- FastAPI for API mode
- Typer or Click for CLI mode
- Pydantic for schemas
- Pandas / Polars for data processing
- NumPy / SciPy / scikit-learn for metrics/anomaly utilities

### 3.2 Data sources

- OpenSearch client for logs
- Prometheus HTTP API for metrics
- CSV/JSON fallback loaders for development
- Kafka metadata from exported topic/consumer data if direct access is not safe
- Kubernetes metadata via exported JSON or API if available

### 3.3 Models

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

### 3.4 Storage

- Local filesystem for MVP.
- SQLite/Postgres for metadata.
- Qdrant/Chroma for vector retrieval if implementing capsule memory.

### 3.5 UI

- CLI first.
- Streamlit for fast demo.
- Optional React/FastAPI later.

---

## 4. Repository Structure

```text
fcapsule-ai/
|-- README.md
|-- pyproject.toml
|-- .env.example
|-- configs/
|   |-- sample_app.yaml
|   |-- model_config.yaml
|   `-- scoring_config.yaml
|-- data/
|   |-- sample/
|   |   |-- logs.jsonl
|   |   |-- metrics.csv
|   |   |-- faults.json
|   |   `-- k8s_metadata.json
|   `-- README.md
|-- fcapsule/
|   |-- __init__.py
|   |-- cli.py
|   |-- api.py
|   |-- schemas/
|   |   |-- capsule.py
|   |   |-- telemetry.py
|   |   `-- evidence.py
|   |-- adapters/
|   |   |-- base.py
|   |   |-- opensearch_adapter.py
|   |   |-- prometheus_adapter.py
|   |   |-- fault_adapter.py
|   |   |-- kafka_adapter.py
|   |   `-- k8s_adapter.py
|   |-- preprocessors/
|   |   |-- timestamps.py
|   |   |-- normalizer.py
|   |   `-- entity_mapper.py
|   |-- models/
|   |   |-- log_template_extractor.py
|   |   |-- metric_anomaly_detector.py
|   |   |-- fault_timeline_builder.py
|   |   |-- embedding_retriever.py
|   |   `-- llm_evidence_ranker.py
|   |-- attention/
|   |   |-- evidence_scoring.py
|   |   |-- selector.py
|   |   `-- exclusion_explainer.py
|   |-- generation/
|   |   |-- capsule_json.py
|   |   |-- capsule_markdown.py
|   |   `-- prompt_templates.py
|   |-- evaluation/
|   |   |-- compression.py
|   |   |-- signal_preservation.py
|   |   |-- hallucination_check.py
|   |   |-- human_eval.py
|   |   `-- model_comparison.py
|   `-- utils/
|       |-- logging.py
|       `-- time_windows.py
|-- tests/
|   |-- test_log_templates.py
|   |-- test_metric_anomalies.py
|   |-- test_capsule_generation.py
|   `-- test_evaluation.py
|-- notebooks/
|   |-- exploratory_log_analysis.ipynb
|   `-- evaluation_results.ipynb
|-- reports/
|   |-- sample_capsule.md
|   `-- evaluation_summary.md
`-- docs/
    |-- architecture.md
    |-- literature_review_notes.md
    `-- university_template_checklist.md
```

---

## 5. Draft Capsule JSON Shape

This schema is only a starting point. It should evolve as the project clarifies what evidence is most useful.

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

---

## 6. Draft Data Models

### 6.1 Log event

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

### 6.2 Metric point

```python
class MetricPoint(BaseModel):
    timestamp: datetime
    metric_name: str
    value: float
    labels: dict[str, str] = {}
```

### 6.3 Fault event

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

### 6.4 Evidence item

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

## 7. Evidence Scoring Proposal

A simple scoring function is enough for MVP. It must be explainable.

### 7.1 Log pattern usefulness score

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

### 7.2 Metric usefulness score

```text
metric_usefulness =
  anomaly_score
+ service_relevance
+ fault_temporal_overlap
+ known_sli_weight
+ change_vs_baseline
```

### 7.3 Fault usefulness score

```text
fault_usefulness =
  severity_weight
+ first_occurrence_weight
+ affected_entity_count
+ duration_weight
+ correlation_with_logs_metrics
```

### 7.4 Telemetry health score

Track if evidence might be incomplete:

- Log generated_at vs indexed_at delay.
- Kafka lag / topic growth.
- Missing logs from pods.
- Prometheus scrape gaps.
- Fault timestamp ordering issues.

This is important because the capsule should not pretend evidence is complete when the pipeline itself was delayed.

---

## 8. LLM Prompting Proposal

### 8.1 Key principle

The LLM should not inspect raw logs directly except for small representative examples. It should receive structured evidence candidates from other models.

### 8.2 Evidence ranking prompt

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

### 8.3 Capsule summary prompt

```text
Generate a concise incident evidence capsule for an engineer.
Do not claim final root cause.
Use only the supplied evidence items.
Cite evidence IDs after each important statement.
Separate strong evidence from weak evidence.
Include telemetry completeness warnings.
```

---

## 9. Development Milestones

### Week 1-2: Project foundation

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

### Week 3-4: Log pipeline

- Implement log normalization.
- Implement log template extraction.
- Compute top templates by volume.
- Compute severity counts.
- Compute change vs baseline if baseline window exists.
- Add representative examples.

Deliverable:

- Log evidence section in capsule.

### Week 5-6: Metrics pipeline

- Implement Prometheus export loader.
- Implement anomaly detection baseline.
- Rank metric anomalies.
- Add metric evidence section.

Deliverable:

- Metric evidence section in capsule.

### Week 7-8: Fault timeline pipeline

- Implement fault loader.
- Build timeline.
- Rank faults by severity/time/entity.
- Correlate faults with log/metric windows.

Deliverable:

- Fault timeline in capsule.

### Week 9-10: LLM evidence ranking

- Implement model abstraction.
- Add DeepSeek/Qwen/local/GPT-compatible providers.
- Build prompt templates.
- Generate capsule summary and next steps.
- Enforce evidence citations.

Deliverable:

- Full Markdown and JSON capsule.

### Week 11-12: Evaluation framework

- Implement compression metrics.
- Implement signal preservation metrics.
- Implement human evaluation form.
- Compare baselines.

Deliverable:

- Evaluation report.

### Week 13-14: Polish and demo

- Add Streamlit UI or polished CLI output.
- Add anonymized sample dataset.
- Add diagrams.
- Add README.
- Prepare final video and report figures.

---

## 10. API / CLI Design

### 10.1 CLI commands

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

### 10.2 API endpoints

```http
POST /investigate
GET /capsules/{capsule_id}
POST /evaluate
GET /health
```

### 10.3 Config file example

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

## 11. Codex / AI Coding Assistant Prompt

Use this as the starting prompt for Codex or another coding assistant when implementation begins:

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

## 12. Open Implementation Questions

These items are intentionally unresolved and should be discussed before or during implementation:

- Which exact log template extractor should be used first?
- Should embeddings be required in the MVP or added after basic log templates work?
- Which anomaly detector should be the baseline: z-score, rolling MAD, Isolation Forest, or another method?
- Which LLM providers are allowed for university/demo work?
- Should the first UI be CLI-only, Streamlit, or FastAPI + simple web UI?
- How strict should the JSON schema be before the evidence shape is validated with sample incidents?
- How much Kubernetes metadata is needed for the MVP?
- How should telemetry delay be represented when only partial exported data is available?
