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
| Episode investigator | configured DeepSeek or OpenRouter core model | selected text, structured alerts, configuration, and bounded metric/log summaries | asks a discriminating read-only check and returns a cited incident assessment |
| Visual specialist | `qwen/qwen3-vl-30b-a3b-instruct` through OpenRouter | a manually supplied PNG, JPEG, or WebP image | extracts visible operational observations, labels, values, and uncertainty from a screenshot |
| Audio specialist | `qwen/qwen3-asr-0.6b` through OpenRouter | a manually supplied short audio recording | produces a transcript that can be considered with retained episode evidence |

The models are orchestrated rather than placed side by side: FCAPSule captures and
preserves an episode deterministically first; a specialist turns an approved artifact
into structured text; only then can the core investigator create a new, immutable
assessment revision that cites the retained artifact-derived observation. The prior
assessment is retained so a reviewer can see what changed.

## Operator Flow

1. FCAPSule captures the incident from Prometheus, OpenSearch, and Kubernetes.
2. The normal evidence-seeking assessment starts independently of any media.
3. In the selected episode, **Add evidence** offers a single-row, auto-growing text
   area with an image attachment control on the left and microphone on the right.
   Paste an image or enter a note; Source details also allows a `.txt`/`.log` import
   or audio-file attachment.
   Text context requires only a validated core model and is ready without a specialist
   call. Images/audio still require their matching validated specialist. Recording
   requires browser microphone support on trusted HTTPS or localhost; audio-file upload also
   works from the LAN HTTP interface. Recording stops after 60 seconds.
   Upload time is automatic. An actual observation time and redaction declaration
   remain optional under Source details; unknown event times stay unknown.
4. The matching specialist processes only that attachment. The original file, derived
   extraction, provider/model identity, usage record, operator note and correction
   history are kept with the episode.
5. **Evidence > Investigation sources > Additional evidence** retains the uploads.
   Small image thumbnails open a labelled in-page dialog, not a new browser tab.
   Close and Escape return focus to the thumbnail; the original is downloadable.
   **Captured telemetry** remains the default Evidence view for alerts, logs,
   performance, configuration and coverage. Citation navigation selects the relevant
   evidence view and provides a return control.
6. The operator chooses **Review new evidence** only after ready evidence exists.
   FCAPSule writes a new assessment revision rather than overwriting the original.
   Timeline shows upload events using upload time, with observation time separately
   labelled, and reassessment requests only when a retained revision exists.

No microphone is opened until the operator presses the recording control. No screenshot,
audio, image, or browser content is collected automatically.
The focusable microphone control explains missing validation, an untrusted connection,
unsupported recording APIs, or denied permission. Certificate errors must be resolved
with a trusted certificate; insecure-origin flags and certificate bypasses are not a
recording solution.

Text and accompanying notes are limited to 16,000 characters and redacted before
storage. Text is an unverified operator claim, not authenticated source telemetry.
The retained UTF-8 original is downloadable; browser downloads use `nosniff` and an
attachment disposition. Model context remains budgeted: the existing 1,800-character
evidence summary and 800-character note limit may omit a long excerpt's tail. Keep the
discriminating observation first; saving a long excerpt does not send it wholesale.

## Capability and Cost Gates

Media upload is unavailable unless all of the following are true:

- the selected core investigator has accepted a bounded JSON canary;
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

Visual extraction reserves at most 800 completion tokens so the specialist can finish
the required structured response. This is an optional, per-attachment cost: its
provider-reported token use, cost, and latency are retained beside the attachment and
do not expand the core investigation's bounded evidence budget.

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
