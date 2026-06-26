# P1 Design Decisions

## Standard Library CLI Instead of a Framework

`argparse` keeps P1 runnable on the available machine without package installation. The console entry point remains compatible with future packaging.

## Dataclass Contract Instead of Runtime Pydantic

The execution environment does not include Pydantic or pip. P1 therefore performs explicit typed validation and returns an immutable `CaseBundle`. The schema boundary can migrate to Pydantic later without changing input files.

## Deterministic Reasoning by Default

External LLM availability, privacy approval, and output stability cannot be assumed. A deterministic evidence-only generator proves orchestration and verification now. The optional DeepSeek comparison uses the same capsule input for `deepseek-v4-flash` and `deepseek-v4-pro`, then scores outputs through a fixed rubric without making the rest of P1 depend on credentials.

## Domains Are Operational Signal Families

P1 uses "domain" to mean fault events, log text, time-series metrics, topology metadata, and LLM reasoning. This is intentionally clearer than saying only "multimodal", because reviewers may otherwise assume media modalities such as text, audio, and image. FCAPSule does not need image generation for P1; the important point is that each telemetry family has a different structure and analysis method.

## Counter Deltas

Prometheus counters are not gauges. Metrics ending in `_total` are transformed into non-negative per-sample increments before baseline comparison, preventing normal cumulative growth from appearing anomalous.

## Explainable Attention Score

P1 records severity, anomaly, time proximity, entity match, rarity, relevance, and repetition penalty. The weights are heuristic and must be calibrated in later evaluation, but every selection is inspectable.

## Derived-Only Archive

The archive exists to preserve useful evidence under retention constraints, not to duplicate all telemetry. Raw source files remain outside the ZIP.

## Static Dashboard Before Full UI

P1 includes a generated `dashboard.html` so the model comparison and domain map are easy to inspect. It is not a production UI, does not require a server, and can be replaced by a richer P5 review interface.
