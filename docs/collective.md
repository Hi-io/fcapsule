# Collective Integration

**FCAPSule investigates; Collective remembers.** Collective is an independent, opt-in
shared memory service with its own repository, HTTP API and PostgreSQL database:
[service repository](https://github.com/Hi-io/estima). FCAPSule remains responsible for
collecting evidence, running any configured LLM investigation, and deciding what
minimized knowledge to publish. Collective validates, stores and retrieves that
knowledge; it does not run LLMs, need model-provider API keys, interpret cases or
incur model-token charges. Any interpretation of retrieved cases happens in the
requesting FCAPSule instance, under that instance's model configuration and cost.

Collective is the current product name. Earlier FCAPSule releases called this
integration Estima, so existing configuration, local API paths and saved settings
continue to work during the rename.

The integration is optional and disabled unless read and/or publication are
enabled. A service outage, missing configuration or empty result must not prevent
FCAPSule from capturing evidence, using its own retained history or completing a
local investigation. This is development integration guidance, not a production
readiness claim.

## Responsibilities

```text
FCAPSule instance A                       FCAPSule instance B
sources -> evidence -> local LLM           sources -> evidence -> local LLM
          |                                             ^
          | minimize and publish                       | retrieve candidates
          +-------------------+-------------------------+
                              v
                  Collective HTTP API
                              |
                              v
                       PostgreSQL
```

- **FCAPSule investigates.** It owns source access, incident capture, per-instance
  capsules, local history, model calls and the interpretation of current and
  retrieved evidence. Provider credentials stay in FCAPSule.
- **Collective remembers.** It accepts versioned, bounded case records, preserves
  observations separately from hypotheses, and returns historical candidates with
  provenance. It does not inspect Prometheus, OpenSearch, Kubernetes or live
  incident sources, and it does not execute actions.
- **Operators choose what to share.** Collective reads and publication are separate
  opt-ins. Published records are a minimized projection, not a capsule archive,
  telemetry backup or proof of anonymization.
- **Instances remain independent.** Shared records do not merge episodes, assert
  a common workload identity or establish a shared cause. Local investigation
  continues when Collective is unavailable.

## Facts, Hypotheses and Similarity

Measurements, log-derived findings, configuration observations and their source
and time provenance are kept in `observations`. Model-generated explanations, if
included, remain separate in `hypotheses` and are unverified context. A hypothesis
is never an observation, even when it appears in repeated cases.

Search is a candidate-retrieval heuristic, not semantic understanding or a
diagnostic model. The current contract ranks exact fingerprints or simple lexical
overlap and can include recent in-scope records; it does not use embeddings or
learn from remediation outcomes. A score is not a probability, and a returned
case is not evidence that two incidents share a cause. FCAPSule must show source,
time, version and observation/hypothesis distinction, then interpret any useful
context using its own current evidence and configured model.

## Data and Privacy

Publishing transfers incident-derived information outside the FCAPSule instance's
storage boundary. Review the actual outgoing case and the organization's sharing,
retention and access policy before enabling it. The producer projection is bounded
and allowlisted: it excludes raw telemetry windows, uploaded media, Kubernetes
Secrets, raw trace spans and capsule ZIPs. It may contain selected observations,
provenance, a bounded summary and separately labeled hypotheses. The service
validates input shape and recognized secret patterns, but it is not a complete DLP
system or an anonymization guarantee. Rare values, timestamps, topology and
combinations of observations may still identify an environment.

The `FCAPSULE_COLLECTIVE_TOKEN` is a credential for the Collective service API; it
is not an LLM API key and is not used for model inference. Collective does not receive
FCAPSule provider keys. Conversely, whenever FCAPSule's configured provider
interprets a retrieved record, that is an ordinary FCAPSule model call and may
incur provider usage and charges.

Collective has an independent data lifecycle. Deleting a local incident does not
delete a previously published remote case. Agree on remote retention, deletion,
backup and export procedures with the Collective operator before sharing real cases.
The current API does not provide a per-case delete endpoint or automatic case
retention policy.

## API Contract

The FCAPSule client speaks Collective's versioned `/v1` API. See the
[service repository](https://github.com/Hi-io/estima) for the service's authoritative
API and deployment contract.

| Operation | Route | Behavior and limit |
| --- | --- | --- |
| Publish a case | `POST /v1/cases` | Creates a versioned case revision. Identical repeats are idempotent; conflicting reuse of a producer/episode/revision is rejected. |
| Search cases | `POST /v1/search` | Returns a bounded set of historical candidates, latest revision per episode, with provenance and a heuristic retrieval score. |
| Read a case | `GET /v1/cases/{id}` | Returns one retained case with observations and hypotheses as separate fields. |
| List/read patterns | `GET /v1/patterns`, `GET /v1/patterns/{id}` | Describes repeated typed-observation co-occurrence; it is not a causal conclusion. |
| Health | `GET /healthz` | Unauthenticated service/database health; it says nothing about FCAPSule sources or investigation health. |

The current case envelope uses `schema_version`, producer `instance_id`,
`episode_id`, timezone-bearing `observed_at`, optional scope labels, summary,
observations and hypotheses. An optional fingerprint supports candidate matching;
it deliberately does not establish that failure mechanisms are equal. The current
FCAPSule projection is capped at 24 observations and 24 KiB; the service accepts
at most 100 observations, 50 hypotheses and 40 KiB per request. A local evidence
reference is provenance from the producer's case and is not guaranteed to resolve
to a live source link from another FCAPSule instance.

The current shared API uses bearer-token authentication. The token is shared by
participants configured with that service; it does not provide per-instance
authorization or tenant isolation. Do not expose one deployment to mutually
untrusted organizations until a separate authorization/isolation model is
implemented and verified.

## FCAPSule Configuration

Configure the Collective endpoint, service token and stable producer identity in
FCAPSule Settings or through environment defaults:

| Environment variable | Purpose |
| --- | --- |
| `FCAPSULE_COLLECTIVE_URL` | Base URL of the separately operated Collective API. |
| `FCAPSULE_COLLECTIVE_TOKEN` | Collective bearer token; not a provider key. |
| `FCAPSULE_COLLECTIVE_INSTANCE_ID` | Stable identity for this publishing FCAPSule state directory; use a distinct value per independent instance. |
| `FCAPSULE_COLLECTIVE_READ` | Opt in to retrieval. Off by default. |
| `FCAPSULE_COLLECTIVE_PUBLISH` | Opt in to publishing. Off by default. |

Saved Settings override environment defaults. For compatibility, the token is
saved in the owner-readable local state file `estima-settings.json`; if absent,
FCAPSule migrates legacy `atlas-settings.json` settings without removing that file. Keep
the ID stable for one instance's publishing history, and do not clone a state
directory into multiple independent publishers without assigning distinct IDs.
The ID is an attribution field, not an authentication credential. Existing
`FCAPSULE_ESTIMA_*` environment variables remain supported, and legacy
`FCAPSULE_ATLAS_*` names remain fallbacks. When both are set, use the
`FCAPSULE_COLLECTIVE_*` values; they take precedence.

The FCAPSule page is `/collective`, and its preferred local API uses
`/api/settings/collective` and `/api/collective/*`. The previous `/estima` page,
`/api/settings/estima` and `/api/estima/*` paths remain compatibility aliases;
older `/atlas` paths remain supported as well. These are FCAPSule-local routes.
The remote Collective API continues to use `/v1`.

The client requires HTTPS except for localhost and in-cluster `.svc` DNS names.
Use a trusted TLS ingress/proxy for remote endpoints. The client applies bounded
request timeouts and response sizes and keeps a local SQLite outbox for retryable
publication. Publication does not hold up live capture. Watch the Collective status
in FCAPSule for unavailable service or failed/pending delivery; neither is the
same as "no relevant case found."

## Operator Workflow

### Explore Collective Memory

The Collective page presents retained knowledge as an interactive map and an
equivalent list. Larger colored nodes are repeated typed observations; smaller
nodes are retained cases. An edge means that the case contains the observation's
same domain, key, typed value and unit. It is not a causal link or an inferred
root cause. Color groups are presentation categories, not learned clusters.

Selecting a node highlights its neighborhood and opens an adjacent detail panel.
Cases retain their summaries, observations, separately labeled hypotheses and
source provenance. Pattern details explain the shared observation and list
recent members. Direct case and pattern links remain shareable. Zoom, pan,
keyboard selection and a list alternative support different exploration styles.

The time control filters the **event times of loaded cases**, not when Collective
learned or ingested them. The instance control isolates contributions visible in
the loaded records. These controls only change the presentation; they do not
publish, modify, delete or re-investigate anything, and make no model calls.

The browser requests up to 20 patterns and expands their existing detail
endpoints with at most three concurrent requests. Each endpoint returns a bounded
set of recent members. Search can add up to 10 matching cases. Counts explicitly
refer to the loaded view, except pattern totals labeled across memory. Revisions
of one producer/episode are deduplicated to the newest loaded revision. Pattern
totals must not be summed to infer a global incident count. There is no claim that
the map contains every record stored by Collective. Loading, unavailable, partial
retrieval and empty states are distinct, with refresh/retry controls.

### Connect Instances

1. Obtain the endpoint and bearer token from the Collective operator. Review who may
   publish and search, what data may leave each instance, and the remote lifecycle.
2. Configure each FCAPSule instance with the same approved endpoint and token,
   but a distinct, stable `FCAPSULE_COLLECTIVE_INSTANCE_ID`.
3. Keep both reads and publication disabled while validating connectivity, schema
   compatibility and the outgoing minimized projection with synthetic cases.
4. Enable only the desired action. Publishing and searching can be opted into
   separately; neither is necessary for local investigation.
5. When FCAPSule retrieves candidates, inspect provenance and facts separately
   from hypotheses. Continue checking current incident evidence; do not treat
   similarity as a verified root cause.
6. Test timeout, authentication failure, incompatible responses and complete
   Collective outage. Confirm local capture, local history and investigation still
   work and that missing shared context is labeled accurately.

### Publication provenance

Incident report data includes a bounded `publication_provenance` list for
outbox records linked to that local episode. Each entry ties the minimized
Collective revision to up to 32 local investigation revision IDs, delivery
state, attempt count and timestamps. `investigation_revision_links_truncated`
marks entries whose older revision links exceed that bound. Local episode and
investigation revision IDs are kept in FCAPSule's SQLite state; they are not
added to the Collective request. The report does not expose the queued payload,
service credential, or raw exception text. A validated remote case ID is
retained only when the successful response provides one.

Delivery `status` values are `queued` (durably waiting, no failure yet),
`retry_scheduled` (a transient failure is waiting for backoff),
`attention_required` (a permanent failure is quarantined), and `published`
(the service returned a successful response). `receipt_status` distinguishes
`not_yet_confirmed`, `remote_id_recorded`, `remote_id_not_returned`, and
`legacy_receipt_unknown`. A successful response without a recognizable case ID
is still `published`; it is not proof that the remote case is absent. A repeated
identical projection keeps its Collective revision and accumulates local
investigation revision links; a changed projection receives a new revision.
Retries reuse the same outbox projection and revision, matching the remote
idempotency key.

Older outbox rows are retained during schema migration. They have no recoverable
local episode link or response receipt, so `local_episode_link_status` and the
receipt remain explicitly unknown rather than being guessed. An empty
incident-level list means no linked delivery receipt is available; it does not
prove that no remote record exists. Deleting a local incident also does not
delete a published Collective case.

## Evaluation and Limits

Evaluate relevance and false matches on a fixed, reviewed dataset with negative
controls, independent reviewers and a no-Collective/local-history baseline. Measure
latency, outages, payload size, privacy exposure and source-expiry behavior.
Report dataset composition, API/schema version, retrieval settings, protocol,
uncertainty and limitations. A successful request or repeated observation pattern
does not establish diagnostic accuracy, causal learning, fewer incidents, faster
resolution, cost savings or safe reduction of source retention.

A synthetic two-instance smoke test was recorded on 2026-09-25 UTC using the
earlier Atlas-branded integrated prototype and an isolated PostgreSQL-backed
service. It exercised publication, retrieval, provenance, separate hypotheses,
source expiry and unavailable-service behavior. That snapshot does not certify the
current independent Collective deployment, access isolation, relevance across
varied incidents or production readiness. Keep screenshots and test records in
ignored `local_reports/`, not in the repository.
