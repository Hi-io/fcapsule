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
- concrete next checks;
- caution against claiming a final root cause.

The score is evidence-useful rather than aesthetic. A longer answer does not automatically score higher.

## Privacy

The API key must be supplied through `DEEPSEEK_API_KEY`. It is not stored in output files. Raw telemetry is already reduced before the LLM comparison step.
