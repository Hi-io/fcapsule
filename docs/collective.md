# Collective Integration

**Collective** is the optional shared-knowledge service used by independent FCAPSule instances. Its [repository](https://github.com/Hi-io/collective) owns the HTTP service, PostgreSQL schema and deployment. FCAPSule owns evidence capture, model inference and the choice of knowledge to publish.

```text
FCAPSule A                     FCAPSule B
local incident                 new incident
    |                              ^
selected knowledge                 | historical leads
    +------------+-----------------+
                 |
          Collective API
                 |
             PostgreSQL
```

Collective stores and retrieves versioned cases without running LLMs or receiving provider keys. Each requesting FCAPSule instance interprets retrieved observations with its own model and budget. This separates shared storage costs from per-instance inference costs and lets local investigation continue when shared memory is unavailable.

## Connect an Instance

1. Deploy Collective using its repository's instructions and obtain its endpoint and an appropriate credential.
2. Set a distinct, stable producer identity for each independent FCAPSule instance. Use a publisher credential bound to that identity, or a read-only credential when publication is disabled.
3. Configure the endpoint and credential in **Settings > Collective**, or use the environment defaults below.
4. Verify connectivity and the outgoing projection with a permitted test case. Enable reads, publication or both according to the sharing policy.

| Environment variable | Purpose |
| --- | --- |
| `FCAPSULE_COLLECTIVE_URL` | Base URL of the separately deployed API |
| `FCAPSULE_COLLECTIVE_TOKEN` | Service bearer credential, not a model-provider key |
| `FCAPSULE_COLLECTIVE_INSTANCE_ID` | Stable, unique publishing identity |
| `FCAPSULE_COLLECTIVE_READ` | Enable retrieval; off by default |
| `FCAPSULE_COLLECTIVE_PUBLISH` | Enable publication; off by default |

Saved Settings override environment defaults. Keep the identity stable across restarts; assign a new identity and matching credential when cloning an instance into an independent publisher. In Kubernetes, use a Secret for the token, not a ConfigMap. The client requires HTTPS except for localhost and in-cluster `.svc` names. Remote endpoints need a trusted certificate.

The local page is `/collective`; settings and browsing use `/api/settings/collective` and `/api/collective/*`. Compatibility settings are retained in `estima-settings.json`. Earlier `FCAPSULE_ESTIMA_*` and `FCAPSULE_ATLAS_*` names/routes remain supported, but `FCAPSULE_COLLECTIVE_*` takes precedence. New installations should use the Collective names.

## Published Knowledge

FCAPSule projects a capsule into a bounded case with producer/episode identity, observation time, scope, source provenance and selected observations. Any included model explanation remains separate in `hypotheses`. Publication excludes raw telemetry windows, original uploads, capsule ZIPs, Kubernetes Secrets and provider credentials.

The client projection is capped at 24 observations and 24 KiB. An identical projection reuses its revision; changed content produces a new revision. A persistent outbox retries transient failures without holding up local capture. Report-level publication provenance links shared revisions to local investigation revisions and shows delivery attempts and receipts.

| Delivery status | Meaning |
| --- | --- |
| `queued` | Waiting for its first delivery attempt |
| `retry_scheduled` | Transient failure; retry is waiting for backoff |
| `attention_required` | Delivery needs operator attention |
| `published` | The remote service accepted the request |

A successful response may omit a recognizable remote case ID; the receipt records that distinction. Inspect delivery state rather than inferring publication from successful connectivity alone.

## Retrieval and Investigation

The shared fingerprint summarizes stable diagnostic features such as alert family, resource kind, finding categories and diagnostic keys. Retrieval ranks candidate cases through fingerprint matching and textual overlap. The investigator then checks relevant measurements or configuration against the current incident. A historical hypothesis is a lead, not a current observation or a probability of the same cause.

The Collective page provides a map and an equivalent list. Case nodes connect to repeated typed observations; selecting either exposes the associated measurements, hypotheses and provenance. Filters operate on loaded cases and their event times. Loaded counts are not a global inventory, and repeated-observation edges are not causal links. Browsing makes no LLM calls.

## Service Contract

The remote service uses `/v1`, distinct from the FCAPSule-local UI API. Consult the Collective repository for the authoritative service schema.

| Operation | Route |
| --- | --- |
| Publish a versioned case | `POST /v1/cases` |
| Search historical candidates | `POST /v1/search` |
| List/read cases | `GET /v1/cases`, `GET /v1/cases/{id}` |
| List/read observation patterns | `GET /v1/patterns`, `GET /v1/patterns/{id}` |
| Withdraw a producer's episode | `DELETE /v1/episodes/{episode_id}` |
| Check service/database health | `GET /healthz` |

Case records include `schema_version`, `instance_id`, `episode_id`, timezone-bearing `observed_at`, observations, separate hypotheses and optional scope/fingerprint fields. Identical publication retries are idempotent; conflicting reuse of one producer/episode/revision is rejected. Search returns bounded candidates with provenance and the latest revision per episode.

## Privacy and Lifecycle

One Collective deployment is a shared trust domain: publisher credentials restrict publication identity, but readers can inspect cases across that deployment. Use separate deployments for mutually untrusted organizations. Review the actual outgoing observations and the remote retention/access policy before enabling publication. Minimization and masking reduce disclosure but do not guarantee anonymity.

**Delete episode and shared memory** coordinates remote withdrawal and local deletion using the original publishing identity. Local records remain until required withdrawal is confirmed. Automatic local retention and the local-only DELETE API do not withdraw remote cases. Collective retention, database backups and previously exported records have their own lifecycle.

## Verify the Connection

- Confirm endpoint health and model-independent local operation.
- Publish an approved test case and check its delivery receipt and producer identity.
- Retrieve it from another instance and inspect source/time provenance and the observation/hypothesis distinction.
- Check that unavailable service, invalid credentials and an empty result are distinguishable.
- Confirm local capture and investigation continue when Collective cannot be reached.

See [Evaluation](evaluation.md) for paired investigation comparisons and [Privacy and retention](data_privacy.md) for the complete local data boundary.
