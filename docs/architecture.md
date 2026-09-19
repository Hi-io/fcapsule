# Architecture

## Runtime Flow

```text
Prometheus firing alert or explicit incident window
                  |
                  v
   Kubernetes discovery + application registry
                  |
                  v
+------------------------------------------------+
| Read-only source adapters                      |
| Prometheus | OpenSearch | Kubernetes | trace probe |
+----------------------+-------------------------+
                       |
                       v
              Normalized incident case
                       |
         +-------------+-------------+
         |             |             |
   FM timeline     PM analysis    log reduction
         |             |             |
         +------ entity alignment ---+
                       |
                       v
        transparent evidence scoring
                       |
             domain-balanced selection
                       |
          grounded reasoning + verifier
                       |
                       v
+------------------------------------------------+
| Capsule store                                  |
| report | JSON | Markdown | evaluation | ZIP    |
+----------------------+-------------------------+
                       |
              Operations / API / CLI
```

## Control Plane

`ControlPlane` coordinates source polling and background capsule jobs and exposes an immutable snapshot to the HTTP API. `LiveSourceCoordinator` discovers Kubernetes workloads, correlates pod identity across Prometheus and OpenSearch, polls firing alerts, and captures one bounded normalized case per new alert. `FCAPSuleStore` persists application, incident, capsule metadata, source settings, and non-secret model preferences in SQLite. External sources may also submit a normalized case through the ingestion boundary. An incident report is generated from deterministic evidence before optional AI reasoning.

## Source Ownership

FCAPSule owns derived evidence. Observability systems own raw telemetry.

- FM alerts may be copied into the capsule because they define the event.
- PM series are analyzed; only anomaly descriptions and selected values are retained.
- Logs are grouped; only anonymized representative lines are retained.
- Topology and relevant configuration facts may be retained.
- Trace availability and derived findings may be retained; raw spans may not.

## Deployment Shape

The current Kubernetes deployment is single-replica:

```text
FCAPSule pod
  |-- HTTP/API server
  |-- source polling and capsule threads
  |-- SQLite + derived artifacts on a PVC
  |-- ConfigMap source configuration
  |-- optional Secret model credential
  |-- read-only ServiceAccount
       |
       +-- Prometheus
       +-- OpenSearch
       +-- Kubernetes API
       `-- future trace backend (query on demand)
```

The ClusterRole can get/list/watch pods, ConfigMaps, and namespaces. It cannot read Secrets. The default Service is a NodePort for local-cluster development. Distributed workers, PostgreSQL, object storage, ingress authentication, multi-cluster registration, and queue-backed scheduling are future scaling work; they do not change the normalized case or capsule contracts.
