# Core LLM Provider Operations

The core provider is used for optional episode investigations. Deterministic capture
and retained reports do not depend on an LLM. The optional image/audio specialists
remain a separate OpenRouter capability; validating one does not validate the core
investigator.

## Select a Provider

Settings selects `deepseek` or `openrouter`. The same selection and model can be read
or changed through `GET`/`POST /api/settings/ai`. Settings persists `ai_provider` in
the local AI configuration. For deployments, `FCAPSULE_LLM_PROVIDER` supplies the
default only when no persisted provider is set. A persisted Settings/API choice takes
priority; if neither is set, FCAPSule defaults to DeepSeek. Set the environment
variable before starting the service.

OpenRouter defaults to `deepseek/deepseek-v4-pro-0813`; its Flash model is an optional
alternative for bounded evaluation. DeepSeek continues to use its configured
DeepSeek model ID. Provider and model selection do not imply that the
model is available or validated: use **Validate model** in Settings before relying on
it.

## Credentials and Privacy

Use `DEEPSEEK_API_KEY` for the DeepSeek core provider and `OPENROUTER_API_KEY` for the
OpenRouter core provider. Enter a replacement key in the core investigator section of
Settings, or supply it through the deployment secret/environment configuration. A
candidate key is checked before it replaces the saved value. Leaving the field blank
keeps the existing credential. The API reports whether a key is configured; it never
returns the key.

Keys entered in Settings are held in the local `state_dir/.env`; provider selection,
model, and budgets are non-secret settings. Credentials are not stored in SQLite or
included in capsule exports. Protect the state directory and deployment secrets as
credentials. Core investigations and optional image/audio evidence use the same
`OPENROUTER_API_KEY` when both are configured through OpenRouter. Their selected
models and validation states remain independent: media validation does not prove
that the core investigator is ready, or vice versa.

## Investigation Behavior and Budgets

An investigation uses the provider and model selected for that attempt. A timeout,
provider error, or invalid response does not trigger an automatic switch to another
provider or model. The attempt retains its observations and failure status; it does
not silently spend a second provider's budget. The operator can validate another
provider/model and explicitly choose **Reassess** after the current attempt finishes.

Keep the shared investigation budget conservative when changing providers. The
default total reserve is 12,000 tokens and the default full-request input cap is
3,200 tokens; the completion limit applies per call. These are token controls, not a
currency cap or a guarantee of provider billing. Providers may price models and
reported usage differently. Start with the defaults, then raise limits only when a
retained case demonstrates that the bounded evidence or response is insufficient.

For evaluation or incident handoff, record the attempt's provider, model, status,
token usage, and investigation reference together. Do not compare outcomes as if they
used the same model when provider/model provenance differs. The offline comparison
workflow in [model comparison](llm_comparison.md) is separate from Operations.

## Smoke Test and Recovery

1. In Settings, select the provider and model, enter its matching key if needed, save,
   and run **Validate model**. Confirm that the core capability is ready; do not infer
   core readiness from the optional Evidence models section.
2. Select a retained test episode and explicitly run **Reassess**. Confirm the report
   completes with citations, and inspect the investigation activity/export for the
   provider, model, status, and token usage recorded for the attempt.
3. If the attempt fails, keep the retained evidence. Check that the selected provider
   matches the credential, the model ID is accepted by that provider, and the service
   can reach the provider endpoint. Check the provider account's access and limits as
   well. The previous assessment remains available.
4. To recover with another provider, select it explicitly, configure and validate its
   matching key, then run **Reassess**. The new attempt uses that selection; no
   automatic fallback or cross-provider retry occurs.

Do not put API keys in issue reports, screenshots, shell history, or exported
investigation records.
