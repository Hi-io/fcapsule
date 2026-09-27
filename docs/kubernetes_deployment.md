# Endterm Kubernetes Deployment

FCAPSule runs alongside an existing observability stack. It does not install Prometheus, OpenSearch, Filebeat or Grafana. The reference deployment uses one replica, a persistent state volume, read-only source access and a ClusterIP service. [Collective](collective.md) is deployed separately from its own repository.

## Prerequisites

- A working `kubectl` context and a persistent volume provisioner.
- Prometheus with the workload metrics used by your alerts, including kube-state-metrics and container metrics for the supplied pod checks.
- OpenSearch with Filebeat-style Kubernetes fields: `@timestamp`, `message`, `kubernetes.namespace` and `kubernetes.pod.name`.
- An image built from the intended source revision, available to the cluster; the source-install option below is also supported.

## Install

Build the root `Dockerfile` from the `endterm` tag and publish to your own approved registry. Set the application image in a local copy of `deploy/kubernetes/fcapsule.yaml` to that image, preferably by digest, rather than leaving the mutable `latest` default. Configure its source URLs, cluster/namespace scope and PVC storage class before applying it. The source tag identifies the snapshot; it does not imply a prebuilt image is published.

The checked-in PVC uses the `manual` storage class. On a single-node development cluster, `deploy/kubernetes/local-single-node-storage.yaml` supplies a matching hostPath PV at `/var/lib/fcapsule`. Use your cluster's managed storage class for other installations.

```bash
# Optional: only for the included single-node manual storage configuration.
kubectl apply -f deploy/kubernetes/local-single-node-storage.yaml

# Apply your configured copy of the runtime manifest.
kubectl apply -f /path/to/fcapsule-endterm.yaml
kubectl -n fcapsule rollout status deployment/fcapsule
kubectl -n fcapsule port-forward service/fcapsule 8765:8765
```

Visit `http://localhost:8765/console`. The terminal running port-forward must remain open. The optional `deploy/kubernetes/prometheus-rule.yaml` supplies a pod-restart alert; it requires the Prometheus Operator CRD and a rule selector that includes its labels. Existing firing alerts can be used without installing that example rule.

## Source Settings

The runtime ConfigMap supplies `FCAPSULE_PROMETHEUS_URL`, `FCAPSULE_OPENSEARCH_URL`, `FCAPSULE_OPENSEARCH_INDEX`, `FCAPSULE_CLUSTER_NAME`, `FCAPSULE_NAMESPACES`, `FCAPSULE_POLL_INTERVAL_SECONDS`, `FCAPSULE_INCIDENT_WINDOW_MINUTES` and `FCAPSULE_AUTO_BUILD_REPORTS`. Saved **Targets** settings override environment defaults. A blank Kubernetes API URL uses the in-cluster ServiceAccount token and CA.

Source URLs are restricted to their configured origins. Add approved alternative origins, including scheme/host/port, to `FCAPSULE_SOURCE_ALLOWED_ORIGINS` and restart before using them. Kubernetes URLs require HTTPS; the mounted token is sent only to trusted Kubernetes origins. Review saved source settings when reusing a state volume.

OpenSearch Basic authentication uses `OPENSEARCH_USERNAME` and `OPENSEARCH_PASSWORD` in a Secret, not the ConfigMap. The ClusterRole can get/list/watch pods, ConfigMaps, namespaces, Services, ServiceMonitors and PodMonitors. It does not read Secrets. Narrow permissions to namespace Roles where appropriate.

In Targets, **Additional resource IDs** maps alert labels to pod labels. An explicit pod is preferred; otherwise configured IDs can select several replicas. Missing or namespace-ambiguous identities remain unmapped. Capture bounds and omissions are recorded with the incident.

## Model and Webhook Credentials

The application consumes the optional `fcapsule-secrets` Secret through `envFrom`. Populate it through your Secret manager or a protected local env file, outside the repository:

```bash
kubectl -n fcapsule create secret generic fcapsule-secrets \
  --from-env-file=/private/path/fcapsule-secrets.env --dry-run=client -o yaml | kubectl apply -f -
kubectl -n fcapsule rollout restart deployment/fcapsule
kubectl -n fcapsule rollout status deployment/fcapsule
```

Supply only the credentials your configuration uses: `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, OpenSearch credentials, `FCAPSULE_GRAFANA_WEBHOOK_TOKEN` and/or `FCAPSULE_COLLECTIVE_TOKEN`. Keep complete Secret values out of shell history and command output. Select and validate models in Settings. Persisted UI credentials in the state directory must also be protected.

To accept Grafana alerts, set the webhook token, enable the receiver in Targets, and configure a Grafana POST contact point at `http://fcapsule.fcapsule.svc.cluster.local:8765/api/webhooks/grafana` with matching Bearer authorization. The receiver is disabled by default. It reuses Kubernetes, Prometheus and OpenSearch capture; Grafana is not a replacement metric store. Repeat notifications are needed for long-running alerts because unresolved webhook state expires after 24 hours without updates. Avoid routing the same rule from two alert inputs unless separate records are intended.

## HTTPS and Console Access

Localhost port-forward supports browser microphone recording. A remote HTTP node IP does not; use trusted HTTPS and normal microphone permission. Optional specialist validation is still required.

The supplied Caddy sidecar terminates TLS on port 8443 and exposes HTTPS NodePort 30767. Obtain a certificate covering the intended host/IP and provision it from private files:

```bash
kubectl -n fcapsule create secret tls fcapsule-tls \
  --cert=/private/path/server.crt --key=/private/path/server.key \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/kubernetes/https-proxy.yaml
kubectl -n fcapsule patch deployment/fcapsule --type=strategic \
  --patch-file deploy/kubernetes/https-proxy-patch.yaml
kubectl -n fcapsule rollout status deployment/fcapsule
```

Visit `https://<certificate-host-or-ip>:30767/console` without certificate warnings. Apply the HTTPS patch after the base manifest/source overlay. Keep the CA private key outside the cluster and repository. Renew the certificate and restart at a suitable time when required; this static example does not renew certificates automatically.

**Login is disabled by default.** Restrict console reachability. To enable shared-account Basic Auth, set `FCAPSULE_CONSOLE_AUTH_REQUIRED=true` and supply `FCAPSULE_CONSOLE_USERNAME` and `FCAPSULE_CONSOLE_PASSWORD` through a referenced Secret. Basic Auth requires HTTPS for remote use. `/healthz` remains public for probes. If a separate proxy is used, set `FCAPSULE_CONSOLE_TRUSTED_PROXY_CIDRS` only to its source CIDR; the supplied sidecar uses loopback.

## Source-Install Deployment

`deploy/kubernetes/dev-overlay.yaml` installs a GitHub source archive into a shared volume using `python:3.12-slim`. It is an alternative to building an image. Its checked-in URL points to mutable `master.zip`; replace it with the exact tested, published commit before applying the overlay.

For a fresh base Deployment without an existing source installer:

```bash
source_sha="$(git rev-parse HEAD)"
source_archive="https://github.com/Hi-io/fcapsule/archive/${source_sha}.zip"
overlay_patch="$(mktemp)"
trap 'rm -f "$overlay_patch"' EXIT
sed "s#https://github.com/Hi-io/fcapsule/archive/refs/heads/master.zip#${source_archive}#" \
  deploy/kubernetes/dev-overlay.yaml > "$overlay_patch"
kubectl -n fcapsule patch deployment/fcapsule --type=strategic --patch-file "$overlay_patch"
kubectl -n fcapsule rollout status deployment/fcapsule
```

Do not reapply that overlay blindly to an existing source-installed deployment: it can overwrite live template settings. Inspect its init-container names, images and command arrays first:

```bash
kubectl -n fcapsule get deployment fcapsule -o jsonpath='{range .spec.template.spec.initContainers[*]}init={.name}{" image="}{.image}{" command="}{.command}{"\n"}{end}'
```

Use the observed zero-based array index and exact current URL in a guarded update. This example requires the existing `install-source` command layout, whose archive URL is element 7:

```bash
source_sha="$(git rev-parse HEAD)"
source_archive="https://github.com/Hi-io/fcapsule/archive/${source_sha}.zip"
init_index=observed-index
current_archive='<copy-exact-current-archive-url>'
patch="[\
{\"op\":\"test\",\"path\":\"/spec/template/spec/initContainers/${init_index}/name\",\"value\":\"install-source\"},\
{\"op\":\"test\",\"path\":\"/spec/template/spec/initContainers/${init_index}/image\",\"value\":\"python:3.12-slim\"},\
{\"op\":\"test\",\"path\":\"/spec/template/spec/initContainers/${init_index}/command/7\",\"value\":\"${current_archive}\"},\
{\"op\":\"replace\",\"path\":\"/spec/template/spec/initContainers/${init_index}/command/7\",\"value\":\"${source_archive}\"}]"
kubectl -n fcapsule patch deployment/fcapsule --type=json --patch "$patch"
kubectl -n fcapsule rollout status deployment/fcapsule
```

Replace both placeholders before running. A plain rollout restart retains the configured source revision; it does not advance a pinned commit.

## Verify and Maintain

```bash
kubectl -n fcapsule get pods,pvc,svc
kubectl -n fcapsule logs deployment/fcapsule -c fcapsule
curl http://localhost:8765/healthz
```

With port-forward active, check readiness, source connections in Targets and an existing retained report. Readiness alone does not validate provider calls or source permissions. Review changes and complete the [test suite](evaluation.md#automated-checks) before deploying a new revision.

Record the current image/source commit and Deployment revision before an update. Use `kubectl rollout history` to inspect a known-good revision before `kubectl rollout undo --to-revision=<number>`. Deployment rollback does not restore separately changed ConfigMaps, Secrets or persistent data. Back up the state volume and protect it as operational data, including staged raw captures and credentials.

Keep Endterm at one replica: SQLite and in-process workers use a local persistence boundary. Retained incidents, raw staging and Collective records follow the [documented independent lifecycles](data_privacy.md).
