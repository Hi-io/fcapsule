# FCAPSule AI

FCAPSule AI is a multidomain telemetry attention engine for cloud incident evidence. It reduces a prepared bundle of alerts, logs, metrics, and infrastructure metadata into a compact, grounded evidence capsule that remains useful after raw telemetry expires.

Prototype 1 (P1) is deliberately local and reproducible. It proves the evidence-selection workflow using files and a CLI before live Prometheus, OpenSearch, Kafka, or Alertmanager integrations are introduced.

In P1, "multimodal" means multiple operational telemetry domains, not media generation. The handled domains are fault events, log text, time-series metrics, topology metadata, and optional LLM reasoning.

## What P1 Does

P1:

- validates a self-contained incident case;
- aligns telemetry by service, namespace, cluster, pod, and optional CNCC UUID;
- anonymizes sensitive values;
- groups noisy logs into Drain-inspired templates;
- detects explainable time-series anomalies;
- builds an alert-centered timeline;
- scores and selects cross-domain evidence;
- generates and verifies evidence-grounded investigation hypotheses;
- optionally compares DeepSeek LLM outputs on the same capsule input;
- writes Markdown and JSON capsules, evaluation results, baselines, a static dashboard, and a ZIP archive.

It does not claim a final root cause, modify production systems, or replace an observability platform.

## Quickstart

Requirements: Python 3.11+ and PyYAML.

```bash
python3 -m fcapsule.cli investigate --case ./cases/case_001 --out ./outputs/case_001
```

Inspect a case without producing output:

```bash
python3 -m fcapsule.cli inspect --case ./cases/case_001
```

Recompute evaluation results:

```bash
python3 -m fcapsule.cli evaluate --case ./cases/case_001 --output ./outputs/case_001
```

Compare the same capsule with DeepSeek models:

```bash
export DEEPSEEK_API_KEY=...
python3 -m fcapsule.cli compare-llms \
  --capsule ./outputs/case_001/capsule.json \
  --out ./outputs/case_001 \
  --models deepseek-v4-flash deepseek-v4-pro
```

Open the local visual review page:

```bash
python3 -m fcapsule.cli dashboard --output ./outputs/case_001
```

Run the optional demo UI:

```bash
python3 -m fcapsule.cli demo-ui --case ./cases/case_001 --out ./outputs/case_001
```

Then open `http://127.0.0.1:8765/`. The UI can rerun P1, capture a fresh synthetic failure, rerun the DeepSeek comparison when `DEEPSEEK_API_KEY` is set, and show the reductions and model comparison as demo-friendly cards and tables.

Run the full test suite:

```bash
python3 -m unittest discover -s tests -v
```

## Reproduce the Synthetic Incident

The repository includes an intentionally unstable checkout service and a telemetry collector. The collector runs healthy traffic, switches the service into a dependency-failure mode, observes the resulting error-rate alert, and writes a complete case bundle.

```bash
python3 scripts/capture_demo_incident.py --output ./cases/case_001
```

The generated data is synthetic and safe to commit. See `docs/data_privacy.md` for boundaries.

## Expected Outputs

An investigation creates:

```text
outputs/case_001/
  capsule.md
  capsule.json
  evidence.json
  evaluation.json
  baselines.json
  llm_comparison.json      # only after compare-llms
  llm_prompt.json          # only after compare-llms
  dashboard.html
  fcapsule_case_001.zip
```

## P1 Limitations

- Inputs are prepared files, not live observability APIs.
- Log parsing is Drain-inspired masking and exact template grouping.
- Metric analysis uses explainable statistical methods rather than a trained forecasting model.
- The default hypothesis generator is deterministic so P1 works without an API key; DeepSeek comparison is an optional P1 evaluation mode.
- The demo UI is optional and local-only; the CLI remains the primary execution interface.
- The sample incident is synthetic and does not establish production RCA accuracy.

## Documentation

- `FCAPSule_AI_Project_Guide.md`: reviewed concept and P1 requirements.
- `PROJECT_DESIGN.md`: implemented architecture and design rationale.
- `DATA_SCHEMA.md`: case and output contracts.
- `EVALUATION_PLAN.md`: baselines, metrics, and review rubric.
- `PROMPTS.md`: grounded reasoning constraints.
- `docs/llm_comparison.md`: DeepSeek comparison workflow and rubric.
- `ROADMAP.md`: evolution beyond P1.
- `docs/p1_usage.md`: detailed operating guide.
- `docs/design_decisions.md`: important P1 trade-offs.

## Roadmap

P2 introduces live Prometheus and OpenSearch adapters. Later phases add Alertmanager triggers, improved scoring, a review UI, retention-aware storage, broader evaluation, and optional downstream AIOps agents. The full sequence is documented in `ROADMAP.md`.
