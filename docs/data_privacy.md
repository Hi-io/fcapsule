# Endterm Privacy and Retention

FCAPSule captures bounded operational evidence, retains an inspectable incident record and optionally shares selected knowledge. Source telemetry, local artifacts and published Collective cases have separate storage and access boundaries.

## Local Storage

| Location under the state directory | Contents | Lifecycle |
| --- | --- | --- |
| `live-cases/<id>/` | Bounded source logs, metric samples, alerts and configuration | Staging TTL, 24 hours by default |
| `capsules/<id>/` | Selected evidence, trends, report, assessments and ZIP | Local incident retention |
| `investigations/<episode-hash>.json` | Context, checks, assessments and usage | Invalidated when a member is deleted; bounded prior-attempt history |
| `evidence/<attachment-hash>/` | Original operator files and extraction artifacts | Local incident retention |
| `imported-capsules/` | Validated imported archives and retained reports | Local retention measured from import |
| `fcapsule.db` | Registry, incident/episode metadata, evidence references and publication state | Incident cleanup; persistent registry and settings |

Raw live captures are stored on disk. Set `FCAPSULE_LIVE_STAGING_TTL_HOURS` from 1 hour to 365 days to control their independent cleanup eligibility. Cleanup runs during control-plane snapshots, no more than once per minute, and removes at most 100 staged cases per pass. It is not an exact deletion deadline. External `ingest-case` input directories are referenced in place and are not deleted by FCAPSule.

Local incident retention defaults to 30 days, configurable from 1 to 3650 days. Age is measured from capture, not the original event time. Archiving changes visibility without extending retention. Exports and backups outside managed state are unaffected by local cleanup.

## Capsule Exports

The ZIP includes allowlisted derived artifacts, representative log lines, retained metric values, provenance and investigation revisions. It excludes normalized raw input files, original image/audio uploads, raw trace spans, model credentials and offline comparison prompts/responses. Selected examples can still contain sensitive information. The [data contract](../DATA_SCHEMA.md#archive-members) lists the format.

An archive manifest verifies file integrity, not the producer's identity. Import only from trusted sources. Legacy repacking does not retrospectively authenticate a file's origin.

## Masking and Source Permissions

Representative log processing masks known identifiers and credential-shaped patterns; configuration processing redacts credential-shaped keys. These are heuristic protections, not comprehensive data-loss prevention. The earlier raw staging layer is not sanitized by the later representative-line transformation.

Kubernetes access is read-only and covers workload metadata, referenced ConfigMaps and monitoring discovery objects. It does not grant access to Secrets. ConfigMaps and logs can nevertheless contain sensitive values under ordinary names. Restrict source permissions, namespace scope and approved source origins to the intended environment.

## Model Disclosure

The core provider receives bounded selected evidence and scrubbed tool observations. When explicitly uploaded and enabled, an image or audio specialist receives the supplied media. Confirm organizational approval for these external disclosures before configuring provider credentials.

The record retains public check questions, structured decisions, visible evidence references, observations, validation results and usage, not private model deliberation. An explicit new-evidence review creates an assessment revision. Citation checking establishes reference integrity; operational conclusions still require engineer review.

## Collective Disclosure and Deletion

Publication sends a minimized case to the separately operated Collective service. Raw windows, source archives, original media and provider keys are excluded. The requesting FCAPSule instance performs any model interpretation of retrieved knowledge. The Collective token authenticates the service API; it is not a model key.

Collective records have independent retention and withdrawal controls. **Delete episode and shared memory** explicitly requests withdrawal using the publishing identity, waits for confirmation where required, and then deletes local records. Automatic local retention and local-only API deletion do not withdraw shared cases. Withdrawal cannot erase downloads, backups or evidence already reused elsewhere.

Participants in one Collective deployment share a trust boundary. Instance-bound publisher credentials are not reader-side tenant isolation. See [Collective](collective.md) for configuration and delivery state.

## Credentials and Access

Keys entered in Settings are saved in the local `state_dir/.env` and loaded into the process environment. They are not returned in API responses, stored in SQLite or included in capsule exports. Collective configuration uses an owner-readable settings file. Restrict filesystem and persistent-volume access; ignored files and file permissions are not encryption. Never commit credentials or captured telemetry.

Console login is disabled by default. Anyone who can reach an unprotected console can read reports and change settings. Keep it on a trusted network or behind access controls. Optional Basic Auth provides a shared account, not individual roles or SSO. Remote HTTPS terminates at the configured proxy, and certificate renewal is the operator's responsibility. See [deployment](kubernetes_deployment.md).

Endterm does not query a live trace backend or retain raw spans. Imported trace-availability metadata describes the captured case, not a current connection check.
