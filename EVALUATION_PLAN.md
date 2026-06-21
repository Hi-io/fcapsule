# FCAPSule AI P1 Evaluation Plan

## Main Question

Can FCAPSule AI reduce noisy incident telemetry into a compact evidence capsule while preserving useful investigation signal?

## Baselines

### Raw telemetry

The reviewer receives the complete case without ranking or reduction. This is the preservation ceiling and the usability/compression floor.

### Keyword filter

Select logs containing `ERROR`, `WARN`, the primary service, or alert name. This tests whether the attention pipeline adds value beyond common filtering.

### Time-window sample

Select the 20 logs nearest to the first alert. This tests whether structured reduction is better than a bounded chronological sample.

### Single-LLM protocol

Provide the same bounded raw sample to one LLM without template grouping or evidence scoring. Record provider/model, prompt, token count, latency, cost, unsupported claims, and reviewer scores. This baseline is not run by default because P1 must remain reproducible without credentials.

## Objective Metrics

| Metric | Definition |
|---|---|
| Log compression | `1 - selected representative lines / raw lines` |
| Template reduction | `1 - grouped templates / raw lines` |
| Token reduction | `1 - estimated capsule tokens / estimated raw tokens` |
| Signal preservation | Selected important signals / identified important signals |
| Metric anomaly preservation | Selected anomalous metrics / identified anomalous metrics |
| Hypothesis grounding | Valid cited evidence IDs / all cited evidence IDs |
| Runtime | End-to-end wall-clock pipeline time |
| Retention survivability | Present required capsule sections / eight required sections |

P1 token estimates use serialized character count divided by four. They are comparative estimates, not provider billing counts.

## Important Signal Definition

Without production RCA ground truth, P1 defines important signal operationally:

- every supplied alert;
- WARN/ERROR/CRITICAL log templates;
- rare templates;
- metric anomalies at or above the configured threshold;
- affected entity labels;
- case-specific expected notes used for manual review.

This measures evidence preservation, not causal correctness.

## Subjective Review

Reviewers compare the capsule with raw and baseline outputs using the form in `docs/p1_evaluation.md`. Questions use a 1-5 Likert scale for clarity, usefulness, trust, omissions, actionability, and expected time savings.

## P1 Acceptance Thresholds

- complete case parses without warning;
- at least one evidence item from alerts, logs, and metrics;
- log compression at least 80% on the reference case;
- important signal preservation at least 90%;
- hypothesis grounding exactly 100%;
- every hypothesis has missing evidence or next checks;
- archive excludes raw telemetry;
- all automated tests pass.

## Limitations and Threats to Validity

- The reference case is synthetic and intentionally clear.
- Automatically identified signals may not match expert judgment.
- A small case exaggerates capsule overhead in token reduction.
- Statistical results depend on the chosen baseline window.
- Likert review is subjective and may have few reviewers.
- P1 evaluates evidence selection, not production root-cause accuracy.
