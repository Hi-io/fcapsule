# Architecture

## Runtime Flow

```text
Prometheus firing alert, optional Grafana webhook, or explicit incident window
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
                       |
              optional Collective client
                / publish  \ retrieve
               /            \
              +--------------+---- network ----> Collective API --> PostgreSQL
```

## Control Plane

`ControlPlane` coordinates source polling and background capsule jobs and exposes an immutable snapshot to the HTTP API. `LiveSourceCoordinator` discovers Kubernetes workloads, maps pod or configured group IDs across Prometheus, OpenSearch and Kubernetes, polls firing Prometheus alerts, and optionally accepts authenticated Grafana notifications. Both inputs use one bounded capture path. `FCAPSuleStore` persists application, incident, capsule metadata, source settings, non-secret model preferences, resource identity and deterministic recurrence keys in SQLite. External sources may also submit a normalized case through the ingestion boundary. An incident report is generated from deterministic evidence before optional AI reasoning. Recurrence preserves separate episodes and can expose up to three earlier retained candidates to the investigator; it never merges incident records or establishes a shared cause by itself.

Live identity resolution prefers an explicit pod. Configured alert-to-pod label mappings (for example CNFC and VNFC) can instead resolve multiple replicas; the retained case records each captured pod and any omitted matches. Cross-application shared IDs become review cues, not asserted common causes. Grafana uses an authenticated, optional webhook and the same bounded capture path; Prometheus remains the metric source.

## Source Ownership

Observability systems are the system of record. FCAPSule owns derived evidence and also stages bounded live inputs until incident cleanup. The statements below describe derived artifacts, not the staging directory.

- FM alerts may be copied into the capsule because they define the event.
- PM series are analyzed; selected values and captured trend samples support retained charts.
- Logs are grouped; only anonymized representative lines are retained.
- Topology and relevant configuration facts may be retained.
- Trace availability and derived findings may be retained; raw spans may not.

Current-format reports are read from retained JSON without loading source files. Legacy reports can be rebuilt from the capsule, but missing raw PM samples cannot be recovered. Background AI investigation is optional and runs once per episode input fingerprint. It preserves workload state early, then accepts bounded model-selected checks and a cited assessment. Observations and token counts are saved incrementally; evidence remains available during queued/running/incomplete states. Deleting a member invalidates shared derived analysis. Citation checking verifies IDs, not truth. Live trace retrieval is future work. See [AI techniques](ai_investigation_techniques.md).

Collective is an independent, optional service in its own repository with a versioned
API and PostgreSQL database. FCAPSule prepares a bounded case projection and makes
any configured model calls locally; Collective stores and returns cases without
running an LLM or receiving provider keys. If a provider interprets retrieved
context, that model call and token usage belong to the requesting FCAPSule
instance. Remote candidates remain historical context: observations and
hypotheses stay distinct, and similarity is not a cause. A Collective timeout,
unavailable endpoint or empty search does not block local capture or history.

## Deployment Shape

The current Kubernetes deployment is single-replica:

```text
FCAPSule pod
  |-- HTTP/API server
  |-- source polling and capsule threads
  |-- optional authenticated Grafana webhook receiver (disabled by default)
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

The Collective service is outside that pod and is deployed from its own repository:

```text
FCAPSule instance(s) -- optional API connection --> Collective API --> PostgreSQL
                                                      no LLM runtime or keys
```

The ClusterRole can get/list/watch pods, ConfigMaps, namespaces, and Services. It cannot read Secrets. The default Service is a NodePort for local-cluster development. Distributed workers, PostgreSQL for FCAPSule metadata, object storage, ingress authentication, multi-cluster registration, and queue-backed scheduling are future scaling work; they do not change the normalized case or capsule contracts. Collective's PostgreSQL database is part of the independent Collective deployment, not the FCAPSule pod or repository. See [Collective Kubernetes integration](collective_kubernetes_integration.md).
