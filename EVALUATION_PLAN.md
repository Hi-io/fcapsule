# FCAPSule Evaluation Plan

## Evaluation Question

Can FCAPSule reduce a large cross-domain incident window while preserving representative diagnostic signal, grounding every investigation claim, and lowering retained size and model context?

## Reference Scenario

The checkout/inventory scenario is the primary reproducible regression case. It must preserve:

- retry-amplification, pool-saturation, and error-budget FM events;
- retry/breaker, pool exhaustion, and lock/deadline log patterns;
- error, latency, retry, pool, and telemetry-health PM groups;
- topology/configuration context;
- verified on-demand trace availability and zero retained raw spans.

The expected chain is an investigation path, not a root-cause label.

## Objective Metrics

| Metric | Definition |
|---|---|
| Log reduction | `1 - selected representative lines / raw log lines` |
| Template reduction | `1 - templates / raw log lines` |
| Token reduction | `1 - estimated capsule tokens / estimated raw tokens` |
| Signal preservation | retained representative signal groups / identified groups |
| PM preservation | retained important PM groups / identified PM groups |
| Grounding | valid cited evidence IDs / all cited IDs |
| Retention completeness | required capsule sections present / required sections |
| Runtime | wall-clock pipeline duration |
| Storage reduction | retained capsule bytes compared with observed raw bytes |

Important signals are representative groups, not every correlated series. Counting every derivative metric as independently important would reward redundancy and conflict with the attention objective.

## Baselines

- random log sample;
- keyword-filtered log sample;
- alert-window sample;
- highest-volume log templates;
- deterministic evidence pipeline;
- optional single-model reasoning over the same capsule;
- same-input multi-model comparison.

## Model Comparison

Enabled models receive identical messages and capsule evidence. Record:

- provider and model;
- prompt;
- finish reason and parse status;
- provider latency and wall-clock latency;
- prompt, completion, and total tokens;
- valid and invalid evidence citations;
- operational domain coverage;
- expected signal-group coverage;
- actionability;
- evidence breadth;
- unsupported final-root-cause language;
- total rubric score.

A model wins only when its total score is measurably higher. Latency and token use remain separate tradeoffs rather than hidden quality bonuses.

## Acceptance Thresholds

For the reference scenario:

- all three FM alerts fire;
- at least 100 logs and 18 PM series are collected in the reduced test configuration;
- raw spans retained equals zero;
- log reduction is at least 90%;
- representative signal preservation is at least 90%;
- citation grounding is 100%;
- retention completeness is 100%;
- the archive contains no raw telemetry;
- the Operations and Incident Lab HTTP routes respond;
- application, incident, and capsule records persist in SQLite;
- the automated suite passes.

## Human Review

Reviewers should score:

- whether the strongest evidence is useful;
- whether important evidence is missing;
- whether redundant evidence occupies capsule capacity;
- whether the hypotheses are appropriately cautious;
- whether next checks are actionable;
- whether the UI makes source availability and selection status understandable.

## Threats to Validity

- Synthetic incidents do not reproduce all production behavior.
- Expected signal groups are authored with scenario knowledge.
- Token estimates are comparative character-based estimates.
- Statistical PM methods may over-score correlated derivatives.
- Model rubric scores measure grounded evidence use, not definitive causality.
- Thread scheduling changes exact counts and timings.

Results must be reported with these limitations.

