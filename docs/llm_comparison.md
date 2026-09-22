# Model Comparison

FCAPSule provides comparison profiles for `deepseek-v4-flash` and `deepseek-v4-pro`. This offline workflow tests differences on identical retained evidence without presupposing a winner. Operations uses one selected model for automatic background assessment, not simultaneous comparisons. Valid citations are reference checks, not proof of diagnosis accuracy.

## Fair-Input Rule

Every model receives:

- the same system instruction;
- the same response schema;
- the same case metadata;
- the same domain summary;
- the same timeline;
- the same selected evidence;
- the same deterministic hypotheses, limitations, and next checks.

Offline replay models never receive different raw samples. The external live Lab runner also records an `input_fingerprint` for the same preserved capsule and a separate bounded tool-observation fingerprint. Sequential runs may legitimately see different current Prometheus, OpenSearch or Kubernetes state; those runs are contextual comparisons, not byte-identical live-input experiments.

## Recorded Fields

- provider and model;
- status and parse error;
- latency and wall-clock time;
- finish reason;
- token usage;
- reasoning-content presence;
- parsed and raw response;
- cited and valid evidence IDs;
- domain and expected-signal matches;
- rubric scores.

## Rubric

The total score combines:

- valid JSON;
- valid evidence citations;
- operational domain coverage;
- expected signal-group coverage;
- depth of concrete terms within each expected signal group;
- actionable next checks;
- breadth of valid primary support;
- caution against unsupported final-root-cause language.

The winner is `null` when scores tie. Model latency and tokens are displayed separately because a faster or cheaper model may still be preferable when quality differences are small.

Use `python3 -m fcapsule.cli rescore-llms --capsule <capsule.json> --out <output>` to apply the current rubric to stored provider responses without making new API calls.

## Configuration

This workflow is deliberately separate from the Operations view. Run it from the CLI or an evaluation environment after a capsule has been produced; credentials remain in `.env` or the deployment secret store.

The deterministic incident report is produced without a provider call. A provider failure, timeout, or malformed model response must not delay or invalidate it.

## Adding Providers

A new provider must implement the chat-client boundary and return normalized content, usage, finish reason, latency, and provider identity. It must use the same prompt builder and scorer for a valid comparison.
