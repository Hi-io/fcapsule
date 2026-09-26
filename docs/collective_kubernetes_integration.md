# Collective Integration in Kubernetes

Collective is a separately operated service, with its own repository, HTTP API and
PostgreSQL database: [service repository](https://github.com/Hi-io/estima). This guide
covers the FCAPSule client side only. It does not install Collective or PostgreSQL
from the FCAPSule repository. Follow the service repository for deployment,
database migrations, backups and lifecycle operations.

## Topology

```text
FCAPSule A -- optional HTTPS/API --> Collective API -- SQL --> PostgreSQL
FCAPSule B -- optional HTTPS/API -->       |
                                          no LLMs
```

Each FCAPSule retains its own evidence and runs its own configured model calls.
Collective only validates, stores and returns bounded case records. It has no
provider credentials and generates no model-token usage. A retrieved case may be
sent to the configured provider by the FCAPSule instance that requested it;
interpretation and any resulting provider charges remain local to that FCAPSule.

## Configure FCAPSule

In **Settings**, set the base URL and service bearer token provided by the Collective
operator. Or supply environment defaults:

```bash
FCAPSULE_COLLECTIVE_URL=https://collective.example.internal
FCAPSULE_COLLECTIVE_TOKEN=<service-bearer-token>
FCAPSULE_COLLECTIVE_INSTANCE_ID=<stable-unique-instance-id>
FCAPSULE_COLLECTIVE_READ=false
FCAPSULE_COLLECTIVE_PUBLISH=false
```

Do not put the Collective token or FCAPSule provider keys in a ConfigMap. Mount
`FCAPSULE_COLLECTIVE_TOKEN` from the cluster's approved Secret manager. This token
authenticates FCAPSule to Collective; it is not an LLM API key. Provider keys remain
in the normal FCAPSule Secret and are never needed by Collective. The endpoint is a
placeholder: use the actual Collective address issued by its operator.

Reads and publication are independent opt-ins and are off by default. Enable one
or both only after reviewing the outgoing case projection and the Collective sharing
policy. Assign each FCAPSule instance a different stable
`FCAPSULE_COLLECTIVE_INSTANCE_ID`, even when instances share one endpoint and token.
Saved Settings override environment defaults. Current client settings are in
`estima-settings.json` for compatibility; older state migrates values from
`atlas-settings.json` when this file does not exist. `FCAPSULE_ESTIMA_*` settings
remain supported, with `FCAPSULE_ATLAS_*` as the earlier fallback; when names
conflict, `FCAPSULE_COLLECTIVE_*` values take precedence.

The client requires HTTPS except for localhost and Kubernetes `.svc` DNS names.
For an in-cluster Collective service, use its service DNS name and the cluster's
approved network controls. For remote access, terminate trusted TLS and use a
hostname covered by the certificate. Do not expose an unauthenticated or
plain-HTTP bearer-token endpoint to an untrusted network.

## Shared-Service Boundary

Any number of FCAPSule instances can point to the same Collective endpoint. Its
current API uses a shared bearer token and does not provide per-instance
authorization or tenant isolation. A shared endpoint is therefore a trust
boundary, not a multi-tenant partition. Restrict network reachability and token
distribution to approved participants; do not combine mutually untrusted
organizations on one deployment until the service's authorization model supports
and verifies that arrangement.

The FCAPSule repo contains no Collective Deployment, database credentials, or
PostgreSQL storage manifest. Collective's operator is responsible for selecting
durable PostgreSQL storage, backup/restore, retention, deletion and service
readiness checks. The service's `/healthz` checks Collective/database health only; it
does not establish that FCAPSule sources or provider calls are healthy.

## Verify and Operate

1. Deploy Collective using the instructions in its [service repository](https://github.com/Hi-io/estima).
2. From each FCAPSule namespace, verify DNS/TLS reachability to the configured
   endpoint and use `GET /healthz` as an initial service check.
3. Configure the token and a unique instance ID on each FCAPSule. Confirm the
   Settings page reports connectivity without enabling publication or search.
4. Use synthetic cases to inspect the minimized outgoing record and validate
   `POST /v1/cases` and `POST /v1/search` before opting in to real data.
5. Test an invalid token, schema/response mismatch, slow response and full
   service outage. Local capture and local historical retrieval must keep
   working; Collective failure must be distinguishable from an empty search.
6. Monitor local pending/failed publication state and Collective's independent
   storage, access, retention, backup and health controls.

FCAPSule currently uses bounded API requests and a local retryable outbox; a
successful connection does not guarantee every case has been published. Deleting
an incident locally does not delete its remote Collective copy. The current service
API has no per-case delete endpoint or automatic retention policy, so agree on
remote disposal procedures before sending real operational data.

This integration test does not prove search relevance, diagnostic accuracy,
causal learning, high availability or production security. Similarity is a lead
for investigation, not a verified cause. The former `/estima` page and
`/api/estima/*` routes remain available as compatibility aliases. See the
[Collective integration guide](collective.md) for API, privacy, lifecycle and
evaluation limits.
