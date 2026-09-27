# Endterm Verification and Evaluation

Evaluation connects FCAPSule's design choices to observable behavior: useful investigation, durable evidence, bounded processing, multimodal extraction, an understandable operator workflow and reusable knowledge. Software checks and model-quality comparisons address different questions.

## Automated Checks

From a virtual environment installed with `python -m pip install -e .`:

```bash
python -m unittest discover -s tests -v
node --check fcapsule/ui/assets/app.js
node --check fcapsule/ui/assets/estima.js
node --test tests/ui_*.test.cjs
```

The Python suite covers input contracts, evidence processing, source scope, token accounting, citation validation, persistence, archives, recurrence, publication and deletion. JavaScript tests cover the operator interface and navigation helpers. The GitHub Actions **Test** workflow runs these commands. UI changes also require browser checks on retained records at desktop/mobile widths, including keyboard and unavailable/loading states.

## Reproduce the Offline Pipeline

```bash
python -m fcapsule.cli inspect --case tests/fixtures/checkout_dependency_failure
python -m fcapsule.cli investigate \
  --case tests/fixtures/checkout_dependency_failure --out .fcapsule/capsules/evaluation
```

Inspect `capsule.json`, `evaluation.json`, `baselines.json` and the archive. Baselines include raw volume, keyword-filtered logs and alert-nearest sampling. The `single_llm` baseline is marked `not_run` by this deterministic pipeline; an LLM comparison is an explicit separate operation.

The fixture's `expected_notes.md` identifies signals to inspect after reduction. It is test data, not a product guide. The separate [FCAPSule Lab](https://github.com/Hi-io/fcapsule-lab) supplies controlled live faults; FCAPSule does not inject faults into monitored workloads.

## Evaluation Questions

| Decision | Comparison | Recorded outcome |
| --- | --- | --- |
| Adaptive investigation | Targeted checks versus single-pass assessment on the same initial incident evidence | Diagnostic usefulness, supported mechanism, next check, tokens and latency |
| Input and total budgets | Different caps on fixed cases under the same comparison settings | Completed assessments, useful outputs and consumed/reserved tokens |
| Core model selection | Identical capsule messages and response schema across model candidates | Structured validity, reference/coverage score, tokens, latency and reported cost |
| Visual and speech models | The same screenshots or recordings, checked against annotated facts/transcripts | Correct fields, identifiers/numbers, word error rate and usage |
| Collective retrieval | Paired investigations with and without relevant cross-instance history | Useful historical leads, discriminating current checks and final assessment |
| Evidence retention | Incident questions answered from capsules after source access is removed | Answerable questions, evidence gaps and measured artifact sizes |
| Reduction and grouping | Raw versus summarized metrics/logs; repeated deliveries and scoped alert episodes | Preserved peaks/boundaries, estimated input size, duplicate handling and false merges/splits |
| Operator workflow | Professional walkthroughs and prototype feedback | Workflow obstacles, requested changes and corresponding implementation decisions |

Diagnostic usefulness can be scored from 0 to 4: invalid/incorrect; repeats the symptom or generic advice; plausible partial cause; useful explanation and next check; or a justified discriminating outcome. Report the distribution and useful-output count alongside an average. A valid citation or heuristic response-quality score is not itself a correct diagnosis.

For retention economics, compare selective capsule retention with extending the full source telemetry window. Record the actual retained bytes separately from any projection using ingest rate, duration and storage assumptions. Comparing only two compact case formats does not answer that retention question.

## Offline Model Comparison

The bundled CLI provides DeepSeek comparison profiles. It is separate from the configurable live investigator and does not automatically enumerate every model available through OpenRouter:

```bash
python -m fcapsule.cli compare-llms \
  --capsule .fcapsule/capsules/evaluation/capsule.json \
  --out .fcapsule/capsules/evaluation \
  --models deepseek-v4-flash deepseek-v4-pro
python -m fcapsule.cli rescore-llms \
  --capsule .fcapsule/capsules/evaluation/capsule.json \
  --out .fcapsule/capsules/evaluation
```

Comparison requires the matching provider credential and makes paid calls; rescoring uses saved responses without new inference. Keep the same prompt, evidence and rubric across candidates. Record exact model IDs, input fingerprints, finish/parse status, citations, scores, provider usage and latency. Sequential live checks may observe different source states, so preserve observation fingerprints as well as the initial input identity.

The scorer assesses structured output, reference validity, operational coverage, diagnostic detail and actionability. It reports ties instead of forcing a winner. Keep quality, cost and speed separate when selecting a configuration.

## Validation Record and Evidence Handling

The [live integration validation](history/live_validation.md) documents the controlled monitoring-discovery run cited in the academic report, including its original model, budget and token measurements. Its original [commit-pinned source](https://github.com/Hi-io/fcapsule/blob/2eff46389a8296c740e8c3ad84f01d8097394e8b/docs/live_validation.md) remains the citation target. It is a dated experiment, not the default configuration for all Endterm runs.

Keep private provider outputs, screenshots and evaluation records under ignored `local_reports/`. Record revisions and settings with each run. For qualitative feedback, separate practitioner observations from measured task outcomes. The report combines this feedback with controlled comparisons; neither software checks nor a selected scenario establishes a universal diagnostic success rate.
