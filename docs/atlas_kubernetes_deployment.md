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
- an administrator-approved container image loader on `worker-1`;
- outbound HTTPS from FCAPSule init containers to GitHub for the chosen source
  archive;
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

## Build And Apply

Build the Atlas image from the integrated repository revision. The cluster
node must have the image locally because the development Deployment deliberately
uses `imagePullPolicy: Never` rather than pulling an unverified public image:

```bash
docker build -f atlas/Dockerfile -t fcapsule-atlas:dev .
docker save fcapsule-atlas:dev | ssh worker-1 'sudo k3s ctr images import -'
```

If `worker-1` uses a different runtime, import the saved image into that
runtime's Kubernetes image namespace. Do not enable pulling for the local tag.

Set `FCAPSULE_SOURCE_REF` in
`deploy/kubernetes/atlas-test/config.yaml` to the full 40-character FCAPSule
commit SHA that contains the Atlas client integration. The `master` value in
the checked-in sample is intentionally rejected by `apply.sh`; an immutable
commit makes the source init container repeatable. The two development pods
independently install that archive into their own ephemeral `emptyDir`.

Then apply and wait for all readiness checks:

```bash
deploy/kubernetes/atlas-test/apply.sh
kubectl get pods,svc,pvc -n fcapsule-atlas-test
```

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

To update FCAPSule, change `FCAPSULE_SOURCE_REF` to a different immutable
commit SHA and run `apply.sh` again. To update Atlas, rebuild/import the image
with the approved worker image loader, then run:

```bash
kubectl -n fcapsule-atlas-test rollout restart deployment/atlas
kubectl -n fcapsule-atlas-test rollout status deployment/atlas --timeout=180s
```

Changes to either Secret require a rollout restart of the consuming
Deployments. The Atlas API token is consumed by Atlas and both FCAPSule pods;
rotate the shared value together and avoid logging request headers.

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
