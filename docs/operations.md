# V1 Operations

## Start and Configure

Install with `python -m pip install -e .`, then run:

```bash
python -m fcapsule.cli serve --host 127.0.0.1 --port 8765 --state-dir .fcapsule
```

Open `http://127.0.0.1:8765/console`. Use another port if it is occupied. See [Kubernetes deployment](kubernetes_deployment.md) for cluster access, HTTPS and credentials.

In **Targets**, configure Prometheus, OpenSearch and Kubernetes, the namespace scope, incident window and polling interval. Save, **Test connections**, then **Sync now**. Application coverage shows the currently discovered workloads. Source URLs must match trusted deployment origins or `FCAPSULE_SOURCE_ALLOWED_ORIGINS`.

An explicit pod label identifies one resource. **Additional resource IDs** maps alert labels to pod labels for component-level scope, such as `cnfc` or `vnfc`. The label names are configurable. Capture records up to four matched pods and discloses omitted matches. Missing or ambiguous identities remain unmapped instead of being assigned to an arbitrary workload.

Optional authenticated Grafana webhooks can supply alerts; Prometheus remains the metrics source. Connection and webhook configuration are in the deployment guide.

## Investigate an Episode

**Operations** groups related alert signals into episodes and orders them by latest activity. The queue includes active and resolved episodes; archiving moves an episode out of the main queue without deleting it. Each episode has a stable reference, shareable link and independently retained member reports.

| View | Use |
| --- | --- |
| Overview | Read the current explanation, supporting observations, next action and expected finding |
| Investigation | Inspect completed checks, alternatives, alert relationships and relevant earlier cases |
| Evidence | Review captured telemetry or investigation sources, including operator attachments |
| Timeline | Compare alert history with separately timestamped investigation activity |

Source buttons navigate to the cited record and provide a return path. Select another alert when an episode contains several captures. Performance charts show the retained interval, labels and units; supported alert rules also preserve the threshold and alert time. Missing samples remain gaps.

The AI investigation starts after member reports are retained. Its activity panel shows progress, questions, observations and usage. New episode evidence can trigger an updated joint assessment. **Reassess** explicitly starts another attempt with the current configuration; prior assessment revisions remain inspectable.

An inconclusive result still retains the observations and next check. Source unavailability, an exhausted allowance and provider failure are different conditions: inspect the recorded reason before retrying. The investigator recommends actions but does not execute remediation.

## Models and Budgets

In **Settings**, select the core provider (`deepseek` or `openrouter`) and a supported model, supply its credential and run **Validate model**. The default core IDs are `deepseek-v4-pro` and `deepseek/deepseek-v4-pro-0813`, respectively. A persisted Settings choice overrides `FCAPSULE_LLM_PROVIDER`; DeepSeek is the fallback when neither is set.

| Setting | Service default |
| --- | --- |
| Full-request estimated input cap | 3,200 tokens |
| Investigation total reserve | 12,000 tokens |
| Optional model-selected checks | 1, configurable from 0 to 4 |
| Credential | `DEEPSEEK_API_KEY` or `OPENROUTER_API_KEY`, matching the provider |

Required observations are separate from optional checks. The completion allowance applies per call. Inspect provider-reported usage alongside the conservative token reserve; changing a model can change both consumption and price. A failed attempt does not silently switch providers. To change providers, select and validate the replacement, then explicitly reassess.

Keys entered in Settings are stored in the protected local `.env`, not SQLite, API responses or capsule exports. An empty replacement field keeps the existing key. Review the [privacy guide](data_privacy.md) before enabling external inference.

## Add Text, Image or Audio Evidence

1. In an episode, choose **Add evidence** and enter a note, paste/attach an image, import a text/log excerpt or attach/record audio.
2. Add observation time and source details when known. Upload time is recorded separately.
3. Wait for the relevant specialist to finish. Inspect its extraction or transcript and add corrections where necessary.
4. Choose **Review new evidence** to create a new cited assessment revision.

Text requires the validated core model. Images and audio also require their corresponding specialist to be configured and validated in **Settings > Evidence models**. They use the OpenRouter credential but have separate model selections and validation states. No media is captured automatically. Microphone recording requires trusted HTTPS or localhost, browser permission and a validated audio capability; an existing audio file can also be uploaded.

Attachments remain under **Evidence > Investigation sources**. Raw image/audio files stay local and are excluded from the default ZIP; derived observations and provenance are retained. The original assessment is not overwritten.

## Patterns and Collective

**Patterns** groups separate retained episodes with matching application, resource and normalized alert identity. It shows recurrence count, first/latest occurrence and observed gaps, with links back to individual episodes. Archived episodes remain part of history until deleted or expired.

**Collective** extends retrieval to selected knowledge from other FCAPSule instances. Reads and publication are independent opt-ins in Settings. Its map/list displays shared observations, cases and provenance; selecting a case does not run a model or modify an incident. See [Collective integration](collective.md).

## External Cases and Portable Capsules

A normalized input directory follows the [data contract](../DATA_SCHEMA.md). Register it for the console, or run the pipeline directly:

```bash
python -m fcapsule.cli ingest-case \
  --case /path/to/normalized-case --app-id payments-api --app-name "Payments API"
python -m fcapsule.cli investigate \
  --case /path/to/normalized-case --out .fcapsule/capsules/example
python -m fcapsule.cli status
```

In Operations, **Build report** processes an ingested case. **Export** downloads the retained capsule ZIP or report JSON and shows the actual file size and retention eligibility date.

Import a trusted capsule into another local state directory:

```bash
python -m fcapsule.cli import-archive \
  --archive /path/to/fcapsule_incident-123.zip --state-dir .fcapsule
```

Import validates the integrity manifest, file hashes, archive paths and supported report format, with a 512 MiB expanded-size limit. It registers a new local record under `imported-capsules/`; re-importing creates another record. Imported reports are source-read-only, their retention starts at import time, and they are excluded from automatic Collective publication. Integrity checking detects corruption but does not authenticate an archive's producer.

For a trusted legacy ZIP without a manifest, create a separate repacked file before importing:

```bash
python -m fcapsule.cli repack-legacy-archive \
  --archive /path/to/legacy.zip --out /path/to/repacked.zip --accept-unverified-origin
```

The acknowledgment is required. Repacking leaves the original unchanged, accepts only allowlisted files and omits executable dashboard HTML. It establishes integrity from repack time, not proof of the original source.

## Retention and Deletion

Incident retention defaults to 30 days from capture and is configurable from 1 to 3650 days. Archiving is reversible and does not extend retention. Bounded raw live captures have their own staging TTL, 24 hours by default. Cleanup is checked during control-plane snapshots, at most once per minute; the displayed date is eligibility rather than an exact deletion deadline.

**Delete episode and shared memory** explicitly removes an episode's local records and requests withdrawal of its published Collective revisions. Local deletion waits for Collective confirmation where necessary. If the publisher identity is mismatched or the service is unavailable, resolve the issue and retry; local evidence remains until withdrawal completes. Finish active investigations and resolve the source fault first, or an active alert may be captured again.

Automatic retention and the local-only deletion API affect local records only. No deletion removes original Prometheus/OpenSearch telemetry, exported downloads, backups or evidence already copied into other investigations. See [privacy and retention](data_privacy.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No incidents | Targets connectivity, namespace scope and firing alerts; pending rules do not open episodes |
| Pod visible but no logs | OpenSearch index and Filebeat fields `@timestamp`, `message`, `kubernetes.namespace`, `kubernetes.pod.name` |
| Source URL rejected | Exact trusted origins in deployment configuration and `FCAPSULE_SOURCE_ALLOWED_ORIGINS` |
| Kubernetes unavailable | ServiceAccount permissions, token and CA mount |
| AI not configured or provider error | Selected provider/model, matching credential, validation and provider availability |
| New evidence unavailable | Core and specialist validation, supported file type, HTTPS and microphone permission |
| Collective pending | Endpoint, instance-bound credential and publication/withdrawal status |

For automated checks and offline model comparison, see [Evaluation](evaluation.md).
