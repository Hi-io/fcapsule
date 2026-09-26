# Data Privacy and Retention

This describes implemented behavior, not a guarantee of complete anonymization or a production security certification.

## Collection and Storage Boundaries

Prometheus, OpenSearch and Kubernetes remain the source systems of record. FCAPSule reads a bounded incident window using read-only access.

| Layer | Contents | Lifetime |
|---|---|---|
| `state_dir/live-cases/<id>/` | Bounded source logs, PM samples, alerts, metadata and configuration snapshot | Independently eligible for cleanup after the configured staging TTL (24 hours by default) |
| `state_dir/capsules/<id>/` | Selected evidence, masked log examples, metric trends, report, assessment, evaluation and ZIP | Same incident lifetime |
| `state_dir/investigations/<episode-hash>.json` | Episode context, scrubbed check results, structured decisions, assessments and usage | Invalidated when an episode member is deleted; up to three previous attempts retained |
| `state_dir/evidence/<attachment-hash>/` | Optional uploaded image/audio, stored owner-readable; derived extraction, note and correction are separately retained in SQLite/manifest | Same incident lifetime; raw media is excluded from default export |
| `state_dir/fcapsule.db` | Application registry, episodes, incident/capsule metadata and non-secret settings | Incident records are cleaned up; registry/settings persist |

Live capture is **not memory-only**. Staged inputs may contain sensitive logs and metrics. They become eligible for independent cleanup after 24 hours by default; set `FCAPSULE_LIVE_STAGING_TTL_HOURS` to configure a value from 1 hour to 365 days. Cleanup runs during control-plane snapshots, no more than once per minute, and removes at most 100 staged cases per pass, so expiry is not an exact deletion deadline. External `ingest-case` directories are referenced without copying; FCAPSule never deletes an input directory outside its managed state directory.

The ZIP contains derived artifacts, including representative log lines, retained chart values, an evidence manifest and portable investigation revision details. It excludes normalized raw input files, raw uploaded image/audio, raw trace spans and the provider credential. Offline comparison prompts/responses can exist alongside artifacts when explicitly requested, but are not included in the ZIP allowlist.

## Retention and Export

Retention defaults to 30 days and is configurable from 1 to 3650 days in Settings. Age is measured from capture (`created_at`), not original event time. Archiving hides an episode; it does not extend retention. Cleanup is checked during control-plane snapshots, at most once per minute, and when retention changes. It is not an exact wall-clock deletion scheduler.

Expired or explicitly deleted incidents lose their managed input/output directories and incident/capsule metadata. Exported copies and backups outside FCAPSule are not affected. Export shows actual ZIP/JSON sizes, cleanup eligibility date and server-side artifact directory. A current report remains readable without reopening its source capture. Rebuilding a legacy/missing report without source metrics uses retained evidence; it cannot recreate lost raw series.

## Masking and Configuration

Log evidence masks known IP, email, UUID, long-identifier and credential-assignment patterns. Variable numbers are also masked for template grouping. These are heuristics, not a comprehensive DLP system. Bounded staging inputs are not protected by the representative-line masking step.

Kubernetes collection reads referenced ConfigMaps and pod context. Credential-shaped configuration keys are redacted. RBAC does not permit reading Kubernetes Secrets. Sensitive values can still be present under innocent-looking keys; review what workloads expose and narrow collection permissions.

## Model Boundary

The optional episode investigation sends compact retained evidence, configuration and additional scrubbed tool observations to the configured core provider (DeepSeek or OpenRouter), not the unrestricted source window. Each attempt can make up to six provider calls, including final evidence review. This is an external disclosure and a paid API operation. Confirm organizational approval before enabling it. Selected evidence and ConfigMap values can still contain sensitive information despite masking.

The investigation retains its bounded context, public check questions, observations, structured model decisions, validation failures and usage metadata. It does not retain private model deliberation. Completed/incomplete investigations are included in the capsule ZIP. Source errors are recorded generically to avoid persisting request credentials. Offline evaluation separately records prompts/responses. Citation validation checks reference integrity, not factual entailment or causal truth. The tool does not execute remediation. Prometheus/OpenSearch retention is not inferred.

## Collective Sharing

When explicitly enabled, the FCAPSule client can send a bounded, minimized case
projection to the separately operated Collective API or retrieve historical
candidates. Collective stores and returns records; it does not call an LLM, receive
provider keys or incur model-token usage. A FCAPSule provider call that interprets
retrieved context remains an ordinary investigation request and may incur
provider charges. The Collective bearer token is service authentication, not a model
credential. Remote cases have a separate lifecycle: local incident deletion does
not automatically withdraw a published Collective record. The service supports
explicit episode withdrawal and optional retention expiry; review the outgoing
projection and remote retention/access/withdrawal policy before enabling sharing;
masking does not promise anonymity. Legacy `FCAPSULE_ESTIMA_*` settings and `/estima` routes remain
supported alongside the Collective names and routes. See [Collective integration](collective.md).

## Secrets and Deployment

Keys entered in Settings are saved in plaintext in `state_dir/.env` and loaded into the process environment. They are not returned by settings APIs, stored in SQLite or included in capsule archives. A root `.env` or mounted environment Secret can also supply credentials. Git ignores `.env` and managed state, but that is not encryption or access control.

The Kubernetes console requires Basic Auth; standalone development without console credentials is loopback-only. The app does not terminate TLS. Remote HTTPS uses the optional Caddy proxy with operator-managed certificates, which must be rotated and renewed by the operator. Basic Auth does not provide per-user roles or SSO, and the application has no audit trail. Keep filesystem/PVC access restricted and protect backups; do not expose the reference service to the public Internet.

## Traces

Normalized external cases can describe source trace availability and retention. No live Tempo/Jaeger adapter is implemented. FCAPSule does not retain raw trace spans; supplied availability metadata must not be mistaken for a currently verified backend connection.
