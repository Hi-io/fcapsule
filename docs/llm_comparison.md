# DeepSeek Model Comparison

P1 can compare `deepseek-v4-flash` and `deepseek-v4-pro` using the exact same capsule input. This is not required for deterministic operation, but it supports the project evaluation question: with the same FCAPSule process and evidence, does a stronger pretrained LLM produce a more grounded and useful investigation note?

## Inputs

The comparison uses `outputs/<case_id>/capsule.json`. It does not send raw telemetry files directly. The prompt includes selected evidence IDs, domains, summaries, metric anomalies, deterministic hypotheses, missing evidence, and next checks.

## Outputs

- `llm_prompt.json`: the exact prompt sent to both models.
- `llm_comparison.json`: model outputs, usage, latency, parse status, scores, winner, and score delta.
- `dashboard.html`: visual comparison page.

## Scoring

The rubric is deterministic:

- JSON validity;
- valid evidence citations;
- explicit coverage of fault events, log text, time-series metrics, and topology metadata;
- expected incident signal coverage;
- evidence breadth in the primary hypothesis;
- concrete next checks;
- caution against claiming a final root cause.

The score is evidence-useful rather than aesthetic. Expected incident signal coverage is weighted most heavily because a response that misses an important operational signal is less useful even if it cites many evidence IDs. Evidence breadth is still recorded, but it is secondary once citations are valid.

If the top two scores tie, P1 records no measurable winner instead of choosing a model by list order.

## Privacy

The API key must be supplied through `DEEPSEEK_API_KEY`. It is not stored in output files. Raw telemetry is already reduced before the LLM comparison step.
