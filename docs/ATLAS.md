# FCAPSule Atlas

**Status: integration in progress.** This guide describes the contract being
implemented between a FCAPSule instance and a separate shared knowledge service. It
is not a claim that a production Atlas deployment or cross-instance evaluation is
available. The current API uses PostgreSQL, bearer-token authentication for
`/v1/*`, `POST /v1/cases` for versioned publication, and `POST /v1/search` for
candidate retrieval. Production retention, deployment and operational guarantees
still require verification. For PostgreSQL setup, service startup and the HTTP
contract, see the [Atlas service guide](../atlas/README.md).

## Purpose

Each FCAPSule instance already keeps its own incident capsules and can compare a
new incident with eligible retained history from that instance. Atlas is a separate,
opt-in service intended to let participating instances publish compact, scrubbed,
versioned cases and retrieve relevant earlier cases from a shared corpus. It adds a
cross-instance memory boundary; it does not replace each instance's capsule store,
source systems, or operator review.

The motivation is continuity. Prometheus and log backends may expire their source
windows, while an incident can recur later or appear in another participating
environment. A retained Atlas case could preserve selected observations and their
provenance after a source window expires, and make cross-instance recurrence visible
as a lead for investigation. Atlas cannot recover telemetry that was never captured
or was not included in a published case.

Similarity is a retrieval cue, not proof that incidents share a root cause. Each
case must keep observed facts distinguishable from unverified hypotheses. Atlas is
not an RCA service, does not certify causal explanations, and does not feed
remediation outcomes back into a causal-learning loop. Any hypothesis retained with
a case remains explicitly unverified context, never an observed fact.

## Boundaries

```text
FCAPSule instance A                         FCAPSule instance B
local sources -> local capsule              local sources -> local capsule
                       \                    /
                        opt-in publish/search
                                  |
                                  v
                         FCAPSule Atlas
                   versioned case records
```

- **FCAPSule remains the investigator.** Source adapters, incident capture,
  investigation, per-instance history and the operator's decision stay local.
- **Atlas is an optional peer service.** A central service outage or timeout must
  not stop incident capture, capsule generation, local investigation, or use of
  local retained history. Shared search is an enhancement, not a dependency.
- **Published cases are minimized.** The producer projects a terminal investigation
  into an allowlisted case, not a copy of raw Prometheus responses, full log windows,
  uploaded media, Kubernetes Secrets, raw trace spans, or a capsule ZIP. It includes
  selected diagnostic facts and provenance; inspect the projection and its
  remaining privacy risk before enabling publication.
- **Cases are versioned.** A record needs to identify its case/schema version and
  preserve source/time provenance so a consumer can interpret it and a future
  schema can be migrated or rejected explicitly. A version label does not prove
  the contents are complete or correct.
- **Evidence and interpretation stay separate.** Captured measurements, log
  examples, configuration facts and source metadata are not interchangeable with
  model-generated hypotheses. Search must return that distinction and provenance.
- **Instances remain separate.** A matching shared case does not merge incidents,
  assert shared workload identity, or declare a common failure mechanism.

## Data and Privacy

Atlas is opt-in because publication transfers incident-derived information out of
the instance's current storage boundary. An operator or organization must explicitly
approve the service endpoint, credentials, publication scope and retention terms
before sending cases. In the producer integration, read and publish are separate
switches and both default to off; configuring a reachable endpoint does not itself
opt in.

The producer publishes a compact projection of a terminal investigation after
local selection. It excludes raw logs, media and free-form source bodies; it carries
allowlisted fault, performance and configuration observations, a summary, and
separate hypothesis entries. Numeric and boolean configuration facts can be
included when they pass the projection rules; raw configuration text is not copied.
The case scope includes anonymized cluster, namespace, service and workload labels
plus configured CNFC/VNFC identifiers. These scope values are identifiers, not
causal claims. The configured `instance_id` is also part of the contract so cases
can be attributed to a participating producer.

Atlas validates the envelope and rejects nested telemetry and secret-looking
inputs. That is a useful boundary, not a comprehensive DLP system. Summary text,
rare values, stable pseudonyms, topology, timestamps and combinations of otherwise
benign observations can still reveal sensitive information or permit
re-identification. Review actual outgoing cases; do not treat the projection as
anonymous public data.

Masking is not a guarantee of anonymization. Selected values, topology, timestamps
and rare incident combinations can still reveal sensitive information or permit
re-identification. Treat published cases as confidential operational data, not as
anonymous public data. Use synthetic or approved test cases for demonstrations.

The service currently uses one configured bearer token for every `/v1/*` route;
it does not bind access to an authenticated instance identity or provide per-instance
RBAC. Anyone holding that token can use the data routes across the shared corpus.
This is not a tenant-isolation boundary. Do not expose one Atlas deployment to
mutually untrusted organizations until a separate authorization and isolation model
is implemented and verified. At minimum, place this prototype inside a trusted
network boundary and scope token distribution to approved participants.

Before production use, operators must establish and document:

- which organization and instances may publish and search;
- who can read, write, administer and delete cases, and how credentials are scoped
  and rotated;
- transport protection and any required network boundary;
- per-tenant or per-organization isolation, data residency, backups and deletion;
- case retention, export, incident response and Atlas outage ownership;
- whether cross-organization sharing is allowed at all.

Do not publish provider keys, credentials, raw uploaded media, Secrets, raw spans,
complete source payloads, or unreviewed operator material. Atlas must not be used to
bypass source-system authorization or an organization's retention policy. Actual
authentication, authorization, encryption, isolation and deletion guarantees are
not stated here until the service deployment contract documents and tests them.

## API Contract

The current service contract uses one bearer token for all `/v1/*` routes.
Publication uses `POST /v1/cases`; search uses `POST /v1/search`. Case and pattern
detail use `GET /v1/cases/{id}` and `GET /v1/patterns/{id}`; pattern listing uses
`GET /v1/patterns`. `GET /healthz` is unauthenticated and checks database readiness.
See the service API reference for exact response fields and error codes.

| Operation | Intended behavior | Required property |
| --- | --- | --- |
| Publish case | `POST /v1/cases` accepts a versioned case revision. An identical repeat returns the existing case; reusing the same producer/episode/revision with different content returns `409 Conflict`. | Bearer token required; request is at most 40 KiB, with at most 100 observations and 50 hypotheses. String scalar values are at most 256 characters; nested telemetry and recognized secret-looking inputs are rejected. |
| Search cases | `POST /v1/search` accepts optional `query`, `scope`, `instance_id`, `fingerprint`, `observed_after`, `before`/`observed_before`, and `limit` (1-10; default 10). `before` maps to `observed_before`; later cases are excluded when it is supplied. If both before fields are set, they must match. | Bearer token required; result count is bounded by `limit`. Results are latest revisions per episode and historical candidates, not a cause determination. `has_more` indicates omitted matches. |
| Read one case | `GET /v1/cases/{id}` returns a retained case by its Atlas ID. | Bearer token required. The returned case contains observations and hypotheses as separate fields. |
| List/read patterns | `GET /v1/patterns` returns repeated typed observation patterns; `GET /v1/patterns/{id}` returns a pattern and up to 10 latest-revision member cases. | Bearer token required. Pattern occurrence and instance counts deduplicate revisions by episode and describe recorded co-occurrence, not causality. `has_more` indicates omitted matches. |
| Health | `GET /healthz` reports health and database readiness without a bearer token. | Health does not query incident sources and does not imply a FCAPSule instance or its sources are healthy. |

The case envelope uses `schema_version` (currently `1`), `instance_id`,
`episode_id`, timezone-bearing `observed_at`, and a `scope` with these optional
keys: `environment`, `cluster`, `namespace`, `service`, `workload`, `cnfc_id`, and
`vnfc_id` (the current producer projection does not populate `environment`).
`summary`, `observations`, and `hypotheses` are separate fields; `fingerprint` is
optional, `revision` defaults to `1`, and `normalization_version` is optional.
The producer fingerprint normalizes alert family, resource kind, finding categories
and diagnostic keys; it excludes variable measurement values and pod names. This
helps identify candidates without asserting that their mechanisms are equal.

Typed observations retain a scalar value and may include a unit, source, observation
time, and a local evidence reference. That reference identifies the source case's
evidence item; it is not guaranteed to resolve to a live source link in a different
FCAPSule instance. Atlas does not query the original Prometheus, OpenSearch or
Kubernetes systems. Case summary is limited to 2,000 characters;
observation keys, kinds, source, units, references, hypothesis statements and
supporting references each have bounded lengths. Observation timestamps must include
a timezone. The service rejects non-scalar/nested observation values, non-finite or
out-of-range numbers, credential-shaped keys and recognized secret patterns. These
checks are not a complete secret scanner.

The current producer caps its projection at 24 observations and 24 KiB even though
the service accepts up to 100 observations and 40 KiB. The service accepts up to
50 hypotheses; the producer may include a bounded, anonymized likely mechanism only
when it can cite retained observations, and labels it as unverified. It does not
invent a numeric confidence when none is available. Hypotheses must never be merged
into observed facts. Pattern aggregates reflect repeated observation tuples, not
adjudicated root causes or remediation outcomes.

Search considers the latest revision per producer/episode among a bounded set of
eligible cases, then ranks by exact fingerprint or simple lexical overlap with the
query. The score is a retrieval heuristic, not a probability or diagnosis; cases
with no matching term can still appear as recent in-scope candidates. It does not
use semantic embeddings or learn from remediation feedback. A consumer must display
provenance and preserve the facts/hypotheses distinction.

No API response may be interpreted as a verified RCA, a recommended remediation, or
evidence that an action succeeded. Atlas does not execute actions. Search results
should be cited as separate historical case context; local evidence remains the
basis for a current investigation. A missing, malformed, or incompatible result is
unavailable context, not a negative health signal.

## Operator Model

The expected operator workflow is:

1. Review the data-sharing policy and explicitly configure the Atlas service and
   approved publication/search scope.
2. Validate connectivity and the negotiated API/schema versions before enabling
   publication.
3. Publish only after local case construction and minimization; observe publication
   success, rejection and retry status without delaying the local incident.
4. During a later investigation, request bounded Atlas candidates separately from
   local history. Inspect each candidate's source, time, version, and evidence.
5. Decide whether a candidate is relevant. Similarity alone never confirms cause,
   and the operator must continue checking current evidence.
6. Monitor Atlas availability, query latency, case volume, retention and privacy
   controls as service operations, independently of FCAPSule source health.

Settings expose `url`, `instance_id`, `token_configured`, `read_enabled`,
`publish_enabled`, `pending_count`, `failed_count`, and `last_error`. Read and
publication are off by default. Producer environment variables are `FCAPSULE_ATLAS_URL`,
`FCAPSULE_ATLAS_TOKEN`, `FCAPSULE_ATLAS_INSTANCE_ID`, `FCAPSULE_ATLAS_READ`, and
`FCAPSULE_ATLAS_PUBLISH`; saved Settings take precedence over environment defaults.
If no instance ID is configured, FCAPSule creates and persists a unique
`fcapsule-<uuid>` for that state directory. Keep it stable for the life of the
producer; cloned state directories that publish independently need distinct IDs.
The ID is not an authentication credential. A saved token is stored in the
owner-only `state_dir/atlas-settings.json`, and the UI masks it. The service
requires `DATABASE_URL` for PostgreSQL and `ATLAS_API_TOKEN` for its shared bearer
token (at least 24 bytes).

The FCAPSule client rejects URLs with embedded credentials and requires HTTPS,
except for localhost and in-cluster `.svc` DNS names. The Atlas app itself does not
provide TLS; terminate TLS at a trusted ingress or proxy for remote use. This
transport check does not provide service authentication beyond the shared bearer
token or tenant isolation.

The producer projects terminal investigations into a local SQLite outbox before
sending. An independent scanner retries transient failures with exponential
backoff; it does not use the live source-monitoring path and does not delay capture.
The outbox accepts at most 1,000 pending cases; when full, new publication is
rejected and an error is reported. The HTTP client defaults to a two-second request
timeout, caps it at ten seconds, and rejects responses above 512 KiB. This provides
bounded, retryable delivery, not a guarantee of publication: monitor
`pending_count`, `failed_count` and `last_error`. The queue shares the local FCAPSule
state lifecycle. A small local revision ledger survives sent-row pruning so a changed
case cannot reuse an earlier Atlas revision. Atlas requires PostgreSQL; migrations run at application
startup.

## Failure and Operational Limits

Atlas is not in the critical path for local investigation. The integration should
use bounded timeouts and bounded result sizes; on timeout, unavailability,
authentication error, schema mismatch or malformed response, FCAPSule must retain
its local result and continue without Atlas context. Operators should be able to
distinguish `Atlas unavailable` from `no relevant candidate found`; neither state
means the current incident is healthy.

Atlas stores published cases in PostgreSQL and has a separate data lifecycle. The
current API has no public delete endpoint or automatic case-retention policy. Local
incident deletion does not delete a published Atlas copy. Plan remote retention,
backup and deletion separately; do not put production data in this prototype until
an approved lifecycle is implemented.

This integration does not make FCAPSule highly available, turn its local SQLite
store into a distributed database, or guarantee Atlas uptime, freshness, search
completeness, tenant isolation, or exact deletion until those properties are
implemented and tested. A shared corpus may be sparse or biased toward incidents
that operators chose to publish. Schema evolution can reduce recall or cause safe
rejection. False matches and stale cases are expected risks to measure.

## Two-Instance Demonstration

Use a disposable Atlas deployment and two isolated FCAPSule instances with synthetic
cases. Do not use production incidents or live customer identifiers for the demo.

1. Start the service using its deployment guide and verify `/healthz`, the shared
   bearer token, and the database migration state. Use one disposable trusted
   network; the current shared token does not isolate tenants.
2. Configure instances A and B independently with distinct stable `instance_id`
   values. Confirm Atlas publication/search are explicitly disabled before opt-in
   and that each instance still works locally.
3. In A, ingest and retain a synthetic incident. Enable publication for this
   disposable test, inspect the outgoing minimized record, then publish it.
4. Give B a different synthetic incident with deliberate overlap in selected facts
   and at least one meaningful difference. Capture it without copying A's raw
   sources or identifiers.
5. Search Atlas from B with `POST /v1/search`. Inspect the returned version,
   provenance, fact/hypothesis separation and retrieval relation/score. Treat the
   score as a heuristic, not a probability. Confirm the UI/workflow calls this a
   historical candidate, not a common cause. Continue with B's local evidence.
6. Disable or stop Atlas and repeat a local capture/search. Confirm capture,
   reporting, local history and operator navigation still work; shared context is
   only marked unavailable.
7. Expire or remove the synthetic source data while retaining the published Atlas
   case according to the test retention policy. Repeat the search and record which
   facts remain available and their original observation times.
8. The current API has no per-case delete operation. For this disposable demo, tear
   down the isolated Atlas database using its operator procedure, then verify that
   the demo corpus is no longer reachable. Do not treat this as a production
   deletion or backup-erasure guarantee.

Exact commands depend on the final service deployment/API contract and should be
added only after they have been tested against two live instances. A mocked API or
unit test does not demonstrate cross-instance operation, access control, deletion,
or outage isolation.

## Evaluation Plan

Atlas utility is an empirical question. Evaluate it with reviewed, synthetic or
approved incident cases before relying on it operationally. Keep a fixed dataset,
record contract versions and retrieval configuration, and include negative controls
whose symptoms look alike but whose causes are known to differ. Do not tune and
score on the same small set without reporting that limitation.

| Dimension | Evidence to collect | Review question |
| --- | --- | --- |
| Retrieval relevance | Top-k candidate relevance labels from independent incident reviewers; compare with no-Atlas/local-history baseline. | Do useful cases appear near the top, and do reviewers agree about relevance? |
| False matches | Rate and examples of top-ranked cases judged materially misleading; include lookalike negative controls. | How often could similarity encourage an unsupported shared-cause assumption? |
| Latency | End-to-end search latency distributions, timeout rate and payload size under expected and burst load. | Does shared retrieval stay within its configured budget without delaying the local report? |
| Outage isolation | Deliberately stop Atlas, add latency, reject credentials and return malformed/incompatible data. | Does local capture/investigation complete, and is the missing shared context labeled accurately? |
| Source expiry | Compare retrieval before and after deleting/expiring test Prometheus/log data while keeping the retained case. | Are the returned facts still inspectable with their original provenance and observation time? |
| Privacy and lifecycle | Inspect outgoing fields, access boundaries, retention/deletion behavior and audit records. | Is the published content approved, minimized and removable within the documented policy? |

Report dataset composition, case versions, opt-in coverage, retrieval method,
reviewer protocol, confidence intervals where sample size permits, failures and
limitations. Atlas search success alone does not establish diagnostic accuracy,
causal learning, fewer incidents, faster resolution, cost savings, or safe reduction
of raw-source retention. Those claims require separate controlled evaluations.

## Current FCAPSule Versus Atlas

The current codebase has per-instance capsule retention and conservative historical
retrieval. It does not imply shared Atlas storage or cross-instance retrieval.
Atlas's producer, service, UI and deployment are separate integration work; this
document describes their intended boundary and marks unsettled contract details as
provisional. Do not describe Atlas as generally available or enabled by default
until the integrated implementation, privacy/security review, operational guide,
two-instance demonstration and evaluation are complete.
