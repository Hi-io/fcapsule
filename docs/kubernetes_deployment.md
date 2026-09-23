# Kubernetes Deployment

## Purpose

FCAPSule runs beside an existing observability stack. It does not install or replace Prometheus, OpenSearch, Filebeat, Grafana, or Alertmanager. Its pod reads bounded data from those systems, resolves the affected Kubernetes workload, and retains derived incident evidence on a persistent state volume.

The state volume also contains bounded raw live captures under `live-cases/` until incident retention/deletion. Protect the whole volume, not just the derived archive. See [storage and privacy](data_privacy.md).

The current live path is:

```text
Prometheus firing alert
        |
        v
namespace + pod identity
        |
        +-- Prometheus range queries -> PM series
        +-- OpenSearch bounded search -> application logs
        `-- Kubernetes API -> PodSpec + referenced ConfigMaps
        |
        v
normalized case -> evidence capsule -> operator report
```

## Prerequisites

- a reachable Kubernetes cluster and working `kubectl` context;
- Prometheus with kube-state-metrics and pod/container metrics;
- OpenSearch containing Filebeat Kubernetes documents;
- OpenSearch fields for `@timestamp`, `message`, `kubernetes.namespace`, and `kubernetes.pod.name`;
- a persistent volume provisioner, or the included single-node development PV;
- the FCAPSule image for a normal deployment, or outbound GitHub access for the development overlay.

## Install

For a single-node development cluster without a StorageClass:

```bash
kubectl apply -f deploy/kubernetes/local-single-node-storage.yaml
```

The hostPath is `/var/lib/fcapsule` on the Kubernetes node. Use a managed StorageClass instead in a multi-node or production cluster.

Install the restart alert and FCAPSule runtime:

```bash
kubectl apply -f deploy/kubernetes/prometheus-rule.yaml
kubectl apply -f deploy/kubernetes/fcapsule.yaml
kubectl rollout status deployment/fcapsule -n fcapsule
```

The default UI is exposed at `http://<node-ip>:30765`. Change the Service to ClusterIP plus an authenticated Ingress for a shared environment.

## Development Overlay

`deploy/kubernetes/dev-overlay.yaml` uses an init container to install the current GitHub `master` branch into an `emptyDir`, then runs it with the same non-root security policy as the base Deployment.

```bash
kubectl patch deployment fcapsule \
  -n fcapsule \
  --type strategic \
  --patch-file deploy/kubernetes/dev-overlay.yaml
kubectl rollout status deployment/fcapsule -n fcapsule
```

Make source changes on a dedicated branch, not on `master`:

```bash
git switch -c codex/your-change
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
node --check fcapsule/ui/assets/app.js
node --test tests/ui_*.test.cjs
git push -u origin codex/your-change
```

Review the diff and wait for the branch's GitHub Actions **Test** workflow to pass.
UI changes also require a browser smoke test with retained records. The browser
smoke script can load branch assets over read-only live data without deploying
them to the shared instance; do not trigger provider calls or mutate incidents
during a presentation-only check.

After verification, merge the tested branch (through a reviewed pull request or a
local merge), then deploy:

```bash
git switch master
git merge --no-ff codex/your-change
git push origin master
kubectl rollout restart deployment/fcapsule -n fcapsule
kubectl rollout status deployment/fcapsule -n fcapsule
```

This profile is for iteration only. Build an immutable image pinned by digest for release deployments.
These steps are a working convention, not server-enforced branch protection.
Protected-branch rules and required checks can be configured separately in GitHub.

## Source Configuration

The base ConfigMap defines:

- `FCAPSULE_PROMETHEUS_URL`;
- `FCAPSULE_OPENSEARCH_URL`;
- `FCAPSULE_OPENSEARCH_INDEX`;
- `FCAPSULE_CLUSTER_NAME`;
- `FCAPSULE_NAMESPACES`;
- `FCAPSULE_POLL_INTERVAL_SECONDS`;
- `FCAPSULE_INCIDENT_WINDOW_MINUTES`;
- `FCAPSULE_AUTO_BUILD_REPORTS`.

The same non-secret values can be changed in **Targets**. UI changes persist on the state volume and override environment defaults after first save. Leaving Kubernetes API URL blank uses the mounted ServiceAccount token and cluster CA.

OpenSearch Basic authentication can be supplied through `OPENSEARCH_USERNAME` and `OPENSEARCH_PASSWORD`. Do not place credentials in the ConfigMap.

## Model Credential

The Deployment references an optional Secret named `fcapsule-secrets`. Create or update it without committing the value:

```bash
kubectl create secret generic fcapsule-secrets \
  -n fcapsule \
  --from-literal=DEEPSEEK_API_KEY='<value>' \
  --from-literal=OPENROUTER_API_KEY='<optional-value>' \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deployment/fcapsule -n fcapsule
```

The console reports only whether a key is configured. A replacement entered in Settings is written to `/var/lib/fcapsule/.env`, never SQLite or the API response.

`OPENROUTER_API_KEY` is optional. It enables only manually supplied image/audio evidence after the core investigator and relevant specialist model pass their own small validation checks. It is not required for normal alert, metric, log, or configuration capture.

## RBAC and Configuration Evidence

The `fcapsule-observer` ClusterRole has read-only access to pods, ConfigMaps, namespaces, and Services. Services resolve explicitly declared, same-namespace dependencies for bounded agent checks; arbitrary endpoints are not followed. It does not grant access to Secrets. For an affected pod, FCAPSule retains:

- pod and workload identity;
- node, phase, readiness, containers, and image references;
- names of referenced ConfigMaps;
- a masked ConfigMap snapshot and content hash.

Restrict the ClusterRole to namespace Roles when cluster-wide discovery is unnecessary.

## Alert Trigger

The included `PrometheusRule` fires `FCAPSulePodRestartDetected` when a non-system workload restarts in a five-minute window. FCAPSule also consumes existing firing Prometheus alerts that carry namespace and pod labels. When the Prometheus rules API is available, FCAPSule captures the matching PromQL expression, pending duration, rule group, and health so the responder can inspect why the alert fired. `Watchdog` and `InfoInhibitor` are ignored.

An incident ID is derived from alert name, start time, namespace, and pod. Repeated polls of the same firing alert are idempotent. A later firing period can create a new incident.

## Verify

```bash
kubectl get pods,pvc,svc -n fcapsule
kubectl logs deployment/fcapsule -n fcapsule -c fcapsule
curl http://<node-ip>:30765/healthz
curl http://<node-ip>:30765/api/state
```

In **Targets**, all three targets should be healthy. **Application coverage** should show each currently discovered workload, its pods, and FM/PM/LOG/CFG status. A removed workload disappears from current coverage after the next sync while its incident history remains available in Operations.

## Current Constraints

- one replica, SQLite, and in-process background threads;
- no UI authentication or authorization;
- no native TLS termination;
- no durable job queue or distributed locking;
- OpenSearch mapping currently targets Filebeat Kubernetes fields;
- Prometheus queries assume kube-state-metrics and container metrics;
- trace backends are not yet connected;
- the NodePort and hostPath manifests are development defaults.

Use a single replica until metadata and jobs move to shared transactional services. Do not expose the current UI directly to an untrusted network.
