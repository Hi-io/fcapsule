# FCAPSule Evaluation Plan

## Evaluation Question

This is an offline research/regression protocol, not the operator UI contract. Historical numbers and dated UI checks live in `docs/history/`; current product behavior is described in `docs/operations.md`.

Can FCAPSule turn noisy cross-domain telemetry into a useful, evidence-backed investigation, retain enough context for a later recurrence, and keep source queries, storage and model usage bounded? Reduction is valuable only when the responder still gets a better diagnostic decision.

## Current Demonstration Set

The separate [FCAPSule Lab](https://github.com/Hi-io/fcapsule-lab) currently qualifies one case per core evidence domain: `poison-job` for logs, `cpu-saturation` for performance, and `response-schema-skew` for configuration. Each has a fault/recovery record and a reviewed investigation that goes beyond restating the alert. The configuration case needed an explicitly incident-pinned reassessment after a later related alert changed episode primacy. See the Lab's [scenario validation](https://github.com/Hi-io/fcapsule-lab/blob/main/docs/SCENARIO_VALIDATION.md) for the exact caveats.

The remaining catalog is a development and regression backlog, not a set of claimed AI successes. The older checkout/inventory and discovery runs remain in [historical records](docs/history/README.md). Do not mix their source revisions, budgets or scores with the current demo set.

## Objective Metrics

| Metric | Definition |
|---|---|
| Log reduction | `1 - selected representative lines / raw log lines` |
| Template reduction | `1 - templates / raw log lines` |
| Token reduction | `1 - estimated capsule tokens / estimated raw tokens` |
| Signal preservation | retained representative signal groups / identified groups |
| PM preservation | retained important PM groups / identified PM groups |
| Reference validity | valid cited evidence IDs / all cited IDs |
| Retention completeness | required capsule sections present / required sections |
| Runtime | wall-clock pipeline duration |
| Storage reduction | retained capsule bytes compared with observed raw bytes |

Important signals are representative groups, not every correlated series. Counting every derivative metric as independently important would reward redundancy and conflict with the attention objective.

Reference validity is not factual entailment or causal accuracy. Reduction measures capsule content, not the total disk footprint of staged live inputs. Model comparisons must retain the same preserved input and fixed rubric, report ties/regressions, and avoid tuning a scenario after observing a desired winner. A sequential live investigation can still collect different current source observations; evaluation records must store whether bounded tool-observation fingerprints match and describe a mismatch as environmental context, not model superiority. Human usefulness review is separate from citation validity.

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

## Qualification Gates

For each scenario proposed as a demo, record the workload/source revision and require:

- a healthy baseline, one owned intervention, the expected firing alert and verified recovery;
- the matching incident in the assessment context, with no unacknowledged episode contamination;
- a completed investigation that identifies a mechanism or a justified uncertainty beyond the alert's wording;
- cited observations that actually support the key claims, plus a non-redundant next check and a named evidence gap;
- retained report and capsule access after capture, with the available domains and source failures shown honestly;
- measured prompt/response tokens, latency, source volume and capsule size, without interpreting reduction alone as diagnostic success.

The independent scenario oracle stays in the Lab. A `ready` response, valid citations or a high automatic rubric score cannot replace operator review. The ZIP must exclude staged raw input files and raw trace spans; selected evidence can still include sensitive log examples or metric values.

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
