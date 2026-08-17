# Model Comparison

FCAPSule initially provides profiles for `deepseek-v4-flash` and `deepseek-v4-pro`. The comparison asks whether a stronger pretrained model makes better grounded use of the exact same evidence capsule.

## Fair-Input Rule

Every model receives:

- the same system instruction;
- the same response schema;
- the same case metadata;
- the same domain summary;
- the same timeline;
- the same selected evidence;
- the same deterministic hypotheses, limitations, and next checks.

Models never receive different raw samples.

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
- actionable next checks;
- breadth of valid primary support;
- caution against unsupported final-root-cause language.

The winner is `null` when scores tie. Model latency and tokens are displayed separately because a faster or cheaper model may still be preferable when quality differences are small.

## Configuration

Use the Operations view to enable models and set maximum tokens. Credentials remain in `.env` or the deployment secret store.

If no credential is loaded, the deterministic capsule still completes and the model stage is marked skipped.

## Adding Providers

A new provider must implement the chat-client boundary and return normalized content, usage, finish reason, latency, and provider identity. It must use the same prompt builder and scorer for a valid comparison.

