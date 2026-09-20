# FCAPSule AI Grounded Reasoning Prompts

FCAPSule uses deterministic evidence processing, an optional automatic runtime assessment and a separate offline comparison workflow. The examples below preserve the research reasoning intent. Exact executable prompts live in `fcapsule/reasoning/incident_briefing.py` and `fcapsule/reasoning/prompts.py`; conceptual instructions are not all mechanically enforceable.

## Current Automatic Assessment

After a report is saved, the configured model receives incident identity, impact, timeline, retained configuration and a compact evidence map. It returns `operator_brief`, `likely_mechanism`, `first_action`, `why_this_first`, `expected_finding`, `mitigation`, `evidence_ids`, and `uncertainty`. Two to five valid references and nonempty, length-bounded text fields are required. Provider failure or rejected output leaves retained evidence accessible.

The model is instructed to distinguish symptoms from mechanisms, offer a discriminating first check and conditional mitigation, and avoid invented facts or commands. These are prompt constraints, not a proof that every accepted sentence is correct.

## Hypothesis Generation

```text
You are an observability investigation assistant.

Generate ranked investigation hypotheses using only the supplied alert context and evidence items.

Rules:
1. Every hypothesis must cite one or more supplied evidence IDs.
2. Use probabilistic language such as "may", "could", or "is consistent with".
3. Never state a final root cause.
4. Do not invent logs, metrics, services, timestamps, or topology.
5. Separate observations from interpretation.
6. List contradicting evidence when present.
7. State evidence that is missing.
8. Provide concrete next checks.
9. Return structured JSON matching the hypothesis schema.
```

## Hypothesis Verification

```text
You verify evidence grounding; you do not create new incident facts.

For each hypothesis:
1. Check that all cited evidence IDs exist.
2. Identify claims that are not supported by cited evidence.
3. Reduce confidence when there is only one supporting item.
4. Reduce confidence when important telemetry is missing.
5. Reject hypotheses with no valid evidence.
6. Return a verdict, adjusted confidence, and verification notes.
```

## Capsule Writing

```text
Write a concise but complete evidence capsule for an engineer.

- Preserve evidence IDs after important observations.
- Clearly separate facts, hypotheses, and missing evidence.
- Do not claim final root cause.
- Include the telemetry window and affected entities.
- Include actionable next checks.
- Include a retention note explaining what remains useful if raw telemetry expires.
- Include measured evaluation results only; never fabricate results.
```

## DeepSeek Same-Input Comparison

```text
You are FCAPSule AI's incident investigation model. Analyze only the supplied evidence.
Do not invent telemetry, do not assert final root cause, and cite evidence IDs exactly.
Return valid JSON only.

Explain the operational telemetry domains separately:
- fault_events: alert/event stream evidence;
- log_text: semi-structured log template evidence;
- time_series_metrics: numeric metric evidence;
- topology_metadata: service, pod, namespace, cluster, and CNCC alignment.
```

## Mechanical Enforcement

The deterministic verifier checks IDs, bounds confidence to `[0, 1]`, marks unknown IDs unsupported and reduces confidence for single-item or incomplete support. The automatic assessment separately validates its schema, text limits, uncertainty and cited report IDs. Offline comparison scores structure, citations, domain/signal coverage, actionability and cautious language. None is a semantic fact-checker or a calibrated root-cause probability estimator.
