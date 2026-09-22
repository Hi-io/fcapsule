# FCAPSule Evaluation Plan

## Evaluation Question

This is an offline research/regression protocol, not the operator UI contract. Historical numbers live in `docs/evaluation_record.md`; current UI checks live in `docs/product_audit.md`.

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

The metric named grounding measures valid references, not factual entailment or causal accuracy. Reduction measures capsule content, not the total disk footprint of staged live inputs. Model comparisons must retain the same preserved input and fixed rubric, report ties/regressions, and avoid tuning a scenario after observing a desired winner. A sequential live investigation can still collect different current source observations; evaluation records must store whether bounded tool-observation fingerprints match and describe a mismatch as environmental context, not model superiority. Human usefulness review is separate from citation validity.

## Baselines

- random log sample;
- keyword-filtered log sample;
- alert-window sample;
- highest-volume log templates;
- deterministic evidence pipeline;
- optional single-model reasoning over the same capsule;
- same-input multi-model comparison.

## Model Comparison

Offline replay/rescoring models receive identical messages and capsule evidence. For the live Lab runner, models receive the same retained capsule fingerprint but may perform sequential bounded source checks. Record:

- provider and model;
- prompt;
- finish reason and parse status;
- provider latency and wall-clock latency;
- prompt, completion, and total tokens;
- valid and invalid evidence citations;
- operational domain coverage;
- expected signal-group coverage;
- within-group signal depth across identity, FM, PM, logs, topology/configuration, trace policy, and uncertainty;
- actionability;
- evidence breadth;
- unsupported final-root-cause language;
- total rubric score.
- a bounded tool-observation fingerprint and whether paired runs observed the same live source state.

A model result is not a product acceptance gate. Differences between Flash and Pro are expected evaluation evidence; neither is forced to reproduce the other. Latency and token use remain separate tradeoffs rather than hidden quality bonuses.

Stored responses can be evaluated again after a rubric revision with `fcapsule rescore-llms`. Re-scoring does not call the provider and records the rubric version.

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
- the Operations and AI settings HTTP routes respond;
- an externally exported normalized case can be ingested and processed;
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
