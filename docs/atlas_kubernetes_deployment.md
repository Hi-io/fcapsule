# Atlas Integration Test Deployment

This overlay provides a private Atlas service, its PostgreSQL database, and two
FCAPSule development instances in the dedicated `fcapsule-atlas-test` namespace.
The instances share the Atlas URL and bearer token, but keep separate SQLite
state, source-install volumes, and Atlas instance identities (`atlas-dev-a` and
`atlas-dev-b`). Live Prometheus/OpenSearch polling is disabled initially. The
services are `ClusterIP` only; use loopback port-forwarding for local access.

## Cluster Requirements

The checked-in overlay is specific to the current single-node test cluster:

- a schedulable node whose `kubernetes.io/hostname` label is `worker-1`;
- outbound HTTPS from init containers to GitHub and the Python package index;
- 2 GiB for PostgreSQL and 1 GiB per FCAPSule state volume at
  `/var/lib/fcapsule-atlas-test/` on `worker-1`.

It uses three static `hostPath` PersistentVolumes and the dedicated
`fcapsule-atlas-test-local` no-provisioner StorageClass because this cluster has
no dynamic provisioner. The paths are isolated from the existing FCAPSule
volume. This is not a portable or production storage pattern; replace
`storage.yaml` with PVCs for the destination cluster's approved StorageClass.

Requests total approximately 640 MiB memory and 400m CPU across the four
long-running containers. FCAPSule source installation is an init container and
has bounded resources; the FCAPSule containers are limited to 512 MiB each.
Nothing in this overlay depends on the Metrics API.

## Prepare Secrets

Do not commit Secret manifests or values. Create these Kubernetes Secrets
through the cluster's approved secret manager, or from protected files stored
outside the repository. The keys are required by the workloads:

- `fcapsule-atlas-runtime`: `DATABASE_URL`, `ATLAS_API_TOKEN`;
- `fcapsule-atlas-postgres`: `POSTGRES_PASSWORD`.

For a one-off development setup, protected env files can be applied before the
overlay:

```bash
kubectl apply -f deploy/kubernetes/atlas-test/namespace.yaml
kubectl -n fcapsule-atlas-test create secret generic fcapsule-atlas-runtime \
  --from-env-file=/secure/path/atlas-runtime.env
kubectl -n fcapsule-atlas-test create secret generic fcapsule-atlas-postgres \
  --from-env-file=/secure/path/atlas-postgres.env
```

The runtime file should define a `DATABASE_URL` for
`atlas-postgres.fcapsule-atlas-test.svc.cluster.local:5432/atlas` and a long,
random `ATLAS_API_TOKEN`. The PostgreSQL file defines `POSTGRES_PASSWORD`; its
value must match the password embedded in the URL-encoded `DATABASE_URL`.
Protect and delete the source files after use. Kubernetes Secret data is not
automatically encrypted at rest unless the cluster enables encryption; RBAC
and etcd encryption still matter.

## Apply And Update

The Atlas and FCAPSule pods use `python:3.12-slim`; each has a source init
container that downloads the same immutable repository archive. Atlas installs
the `atlas/` package and its requirements into its own `emptyDir`; each
FCAPSule pod independently installs the root package into another private
`emptyDir`. No custom image build or node-level image import is required. The
chosen commit must be reachable from `Hi-io/fcapsule` so GitHub can serve its
archive.

Create the external Secrets first, then apply with the commit SHA that contains
both the Atlas package and FCAPSule client integration:

```bash
SOURCE_REF="$(git rev-parse HEAD)" deploy/kubernetes/atlas-test/apply.sh
kubectl get pods,svc,pvc -n fcapsule-atlas-test
```

`apply.sh` rejects mutable branch names and non-SHA values. It materializes the
source SHA in a runtime ConfigMap, applies the Kustomize overlay, restarts the
deployments so environment-based config is refreshed, then waits for rollout.
The same source revision is used by Atlas and both FCAPSule instances.

The Atlas `/healthz` readiness probe includes a PostgreSQL check. FCAPSule
readiness uses `/healthz`. Probes run against loopback from inside each pod. The
Atlas data API uses the configured bearer token; `/healthz` does not require
authentication.

## Optional Network Policies

If the cluster CNI enforces Kubernetes NetworkPolicy, the separate overlay
restricts Atlas ingress to the FCAPSule pods and PostgreSQL ingress to Atlas.
It is intentionally not part of the default deployment:

```bash
kubectl apply -k deploy/kubernetes/atlas-test/network-policy
```

These are ingress-only policies; they do not restrict egress, so FCAPSule's
source init containers and Atlas database connections continue to work. Verify
the CNI implements NetworkPolicy before applying them. Remove only these
policies with `kubectl delete -k deploy/kubernetes/atlas-test/network-policy`
if support or behavior differs from expectations.

## Access And Updates

Port-forward the UI to loopback. Keep the command running while using the
browser, then use `http://localhost:8765/console`:

```bash
kubectl -n fcapsule-atlas-test port-forward service/fcapsule-dev-a 8765:8765
```

Choose `fcapsule-dev-b` to inspect the second isolated state. Atlas can be
checked separately with `kubectl -n fcapsule-atlas-test port-forward
service/atlas 8080:8080` and `curl http://127.0.0.1:8080/healthz`.

To update Atlas and FCAPSule together, pass a different immutable commit SHA
and run `apply.sh` again:

```bash
SOURCE_REF=<commit-sha> deploy/kubernetes/atlas-test/apply.sh
```

Changes to either Secret require a rollout restart of the consuming
Deployments. The Atlas API token is consumed by Atlas and both FCAPSule pods;
rotate the shared value together and avoid logging request headers.

## Long-Lived Atlas Service

`deploy/kubernetes/atlas/` is the Atlas-only profile for a shared service in the
separate `fcapsule-atlas` namespace. It has no worker selector or `hostPath`;
PostgreSQL uses a 10 GiB PVC. Before use, replace the
`replace-with-storage-class` value in `storage.yaml` with the cluster's
approved durable StorageClass. Its `apply.sh` refuses to proceed while the
placeholder remains.

Create namespace-scoped Secrets in `fcapsule-atlas` through the approved secret
manager, or from protected env files outside the repository:

```bash
kubectl apply -f deploy/kubernetes/atlas/namespace.yaml
kubectl -n fcapsule-atlas create secret generic atlas-runtime \
  --from-env-file=/secure/path/atlas-runtime.env
kubectl -n fcapsule-atlas create secret generic atlas-postgres \
  --from-env-file=/secure/path/atlas-postgres.env
```

Use the keys `DATABASE_URL` and `ATLAS_API_TOKEN` in `atlas-runtime.env`, and
`POSTGRES_PASSWORD` in `atlas-postgres.env`. The database URL should use
`atlas-postgres.fcapsule-atlas.svc.cluster.local:5432/atlas` and the matching
URL-encoded password. Apply a reachable immutable source commit:

```bash
SOURCE_REF=<commit-sha> deploy/kubernetes/atlas/apply.sh
```

FCAPSule instances in other namespaces can use
`http://atlas.fcapsule-atlas.svc.cluster.local:8080` and the same Atlas bearer
token. This service remains a single Atlas and PostgreSQL replica. Services are
`ClusterIP`; it is not TLS termination or user authentication for the FCAPSule
UI. Add cluster-appropriate NetworkPolicy and authenticated TLS ingress only
after validating the network and identity controls. Use
`deploy/kubernetes/atlas/cleanup-runtime.sh` to stop the processes while keeping
the database PVC and data.

## Cleanup And Limits

To stop the four workloads without removing their data, run
`deploy/kubernetes/atlas-test/cleanup-runtime.sh`. It leaves the namespace,
Secrets, PVCs, PVs, and hostPath directories intact. It is safe to apply again
later with `apply.sh`.

The PV reclaim policy is `Retain`. Deleting the namespace or PVCs does not erase
the files on `worker-1`; it releases the PVs and leaves the data directories at
the paths above. Do not run `kubectl delete -k` for routine cleanup. Full
decommissioning requires an explicit data-disposal decision, a backup decision,
deleting the named PVCs and then PVs/StorageClass, and a separate verified
cleanup of only the dedicated host paths on `worker-1`.

This is an isolated development environment, not a production exposure profile:
FCAPSule has no built-in user authentication, Atlas has no TLS in this overlay,
and bearer-token transport is plain HTTP inside the cluster. ClusterIP and
loopback port-forwarding reduce network exposure but do not replace network
policies, TLS, RBAC, storage encryption, backups, or a secret manager. Do not
expose these Services through a public Ingress or NodePort.
