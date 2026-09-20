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
| Prometheus | OpenSearch | Kubernetes            |
+----------------------+-------------------------+
                       |
                       v
       Bounded case staged in live-cases/
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
       deterministic reasoning + ID verifier
                       |
                       v
+------------------------------------------------+
| Capsule store                                  |
| report | JSON | Markdown | evaluation | ZIP    |
+----------------------+-------------------------+
                       |
              Operations / API / CLI
                       |
       episode investigator (optional model)
                       |
       bounded read-only checks -> observations
                       |
       cited assessment + progress + token usage
```

## Control Plane

`ControlPlane` coordinates source polling and background capsule jobs and exposes an immutable snapshot to the HTTP API. `LiveSourceCoordinator` discovers Kubernetes workloads, correlates pod identity across Prometheus and OpenSearch, polls firing alerts, and captures one bounded normalized case per new alert. `FCAPSuleStore` persists application, incident, capsule metadata, source settings, and non-secret model preferences in SQLite. External sources may also submit a normalized case through the ingestion boundary. An incident report is generated from deterministic evidence before optional AI reasoning.

## Source Ownership

Observability systems are the system of record. FCAPSule owns derived evidence and also stages bounded live inputs until incident cleanup. The statements below describe derived artifacts, not the staging directory.

- FM alerts may be copied into the capsule because they define the event.
- PM series are analyzed; selected values and captured trend samples support retained charts.
- Logs are grouped; only anonymized representative lines are retained.
- Topology and relevant configuration facts may be retained.
- Trace availability and derived findings may be retained; raw spans may not.

Current-format reports are read from retained JSON without loading source files. Legacy reports can be rebuilt from the capsule, but missing raw PM samples cannot be recovered. Background AI investigation is optional and runs once per episode input fingerprint. It preserves workload state early, then accepts bounded model-selected checks and a cited assessment. Observations and token counts are saved incrementally; evidence remains available during queued/running/incomplete states. Deleting a member invalidates shared derived analysis. Citation checking verifies IDs, not truth. Live trace retrieval is future work. See [AI techniques](ai_investigation_techniques.md).

## Deployment Shape

The current Kubernetes deployment is single-replica:

```text
FCAPSule pod
  |-- HTTP/API server
  |-- source polling and capsule threads
  |-- SQLite + staged captures + derived artifacts on a PVC
  |-- ConfigMap source configuration
  |-- optional Secret model credential
  |-- read-only ServiceAccount
       |
       +-- Prometheus
       +-- OpenSearch
       +-- Kubernetes API
       `-- future trace backend (query on demand)
```

The ClusterRole can get/list/watch pods, ConfigMaps, namespaces, and Services. It cannot read Secrets. The default Service is a NodePort for local-cluster development. Distributed workers, PostgreSQL, object storage, ingress authentication, multi-cluster registration, and queue-backed scheduling are future scaling work; they do not change the normalized case or capsule contracts.
