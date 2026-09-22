# Optional Multimodal Evidence

## Purpose

FCAPSule's primary job is to preserve and investigate an operational episode before
the source telemetry changes or expires. Its core evidence remains alert events,
numeric performance series, application logs, and Kubernetes configuration. Some
investigations also begin with a human-produced artifact: a screenshot of a target
state, a dashboard panel, an alerting view, or a short spoken observation recorded
during handover. This feature accepts those artifacts as **optional additional
evidence**, without turning FCAPSule into a general media collector.

## Models and Data Domains

The integrated workflow uses independently pretrained models over genuinely different
data shapes:

| Role | Default model | Data it receives | Operator value |
|---|---|---|---|
| Episode investigator | configured DeepSeek-compatible model | selected text, structured alerts, configuration, and bounded metric/log summaries | asks a discriminating read-only check and returns a cited incident assessment |
| Visual specialist | `qwen/qwen3-vl-8b-instruct` through OpenRouter | a manually supplied PNG, JPEG, or WebP image | extracts visible operational observations, labels, values, and uncertainty from a screenshot |
| Audio specialist | `qwen/qwen3-asr-0.6b` through OpenRouter | a manually supplied short audio recording | produces a transcript that can be considered with retained episode evidence |

The models are orchestrated rather than placed side by side: FCAPSule captures and
preserves an episode deterministically first; a specialist turns an approved artifact
into structured text; only then can the core investigator create a new, immutable
assessment revision that cites the retained artifact-derived observation. The prior
assessment is retained so a reviewer can see what changed.

## Operator Flow

1. FCAPSule captures the incident from Prometheus, OpenSearch, and Kubernetes.
2. The normal evidence-seeking assessment starts independently of any media.
3. In the selected episode, the operator may choose **Add evidence** and attach an
   image or record/upload short audio. They can supply observation time, context, and
   whether the copy has been redacted.
4. The matching specialist processes only that attachment. The original file, derived
   extraction, provider/model identity, usage record, operator note and correction
   history are kept with the episode.
5. The operator chooses **Update investigation** only after a ready extraction exists.
   FCAPSule writes a new assessment revision rather than overwriting the original.

No microphone is opened until the operator presses the recording control. No screenshot,
audio, image, or browser content is collected automatically.

## Capability and Cost Gates

Media upload is unavailable unless all of the following are true:

- the core DeepSeek-compatible investigator has accepted a bounded JSON canary;
- the selected vision or audio specialist has accepted its own minimal canary;
- the relevant local API key is present; and
- the type matches a supported MIME type and size limit.

The UI directs an operator to Settings when the gate is closed and only offers the
enabled media type. The server repeats the check before saving or processing a file;
the browser is never the authorization boundary. Provider errors are stored as a
bounded limitation without exposing credentials.

Validation is deliberately small. It proves that the configured credential/model path
can accept the modality; it does not claim that a model is accurate for every screenshot
or recording. Settings shows the capability state but never returns a credential.

## Provenance and Trust

An uploaded artifact is operator-supplied, may refer to an earlier time, and can be
wrong or incomplete. FCAPSule labels it as such. The core investigator is instructed to
treat telemetry, uploads, and prior model text as untrusted input rather than commands.
It may cite a ready extraction but must not convert it into a causal claim without
supporting retained evidence. A correction is additive; it does not erase the original
extraction or earlier assessment revision.

The specialist output is compacted into a bounded observation before the next model
call. On an explicit evidence revision, its derived `A-...` observation is prioritized
in the prompt ledger and every call records the visible evidence IDs. Raw source
telemetry is never sent wholesale merely because a media item was added. See [AI investigation techniques](ai_investigation_techniques.md) for the
full-request token reserve and [data privacy](data_privacy.md) for retention boundaries.

## Evaluation Protocol

Evaluation should use a fixed, versioned incident set and score the same core
assessment with and without an artifact where the artifact is genuinely relevant. For
each model configuration, record:

- capability validation result, model identifier, latency, and provider-reported usage;
- extraction correctness against a human-annotated visible fact/transcript sample;
- whether the additional evidence changed a cited finding, next action, or unresolved
  discriminator;
- unsupported claims, missing provenance, and operator usefulness; and
- the incremental token/cost/latency impact over the deterministic capture.

Do not use media merely to make an answer look richer. A screenshot that duplicates an
already retained alert, or audio that only repeats the incident title, should not change
the assessment and is a valid negative result. This makes the feature evaluable without
claiming that multimodal evidence is always necessary.
