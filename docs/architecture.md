# Architecture

## Runtime Flow

```text
Alert trigger or explicit incident window
                  |
                  v
        Application registry (SQLite)
                  |
                  v
+------------------------------------------------+
| Read-only source adapters                      |
| FM | PM | logs | topology/config | trace probe |
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

`ControlPlane` coordinates background jobs and exposes an immutable snapshot to the HTTP API. `FCAPSuleStore` persists application, incident, and capsule metadata in SQLite. The simulation lab and Operations UI are two clients of the same state. An incident report is generated from deterministic evidence before the capsule is marked ready; offline model evaluation cannot block that report.

## Source Ownership

FCAPSule owns derived evidence. Observability systems own raw telemetry.

- FM alerts may be copied into the capsule because they define the event.
- PM series are analyzed; only anomaly descriptions and selected values are retained.
- Logs are grouped; only anonymized representative lines are retained.
- Topology and relevant configuration facts may be retained.
- Trace availability and derived findings may be retained; raw spans may not.

## Deployment Shape

The current service is single-node and local-first:

```text
python process
  |-- HTTP/API server
  |-- background jobs
  |-- SQLite metadata
  |-- local derived artifacts
  `-- read-only adapter calls
```

The target Kubernetes shape is:

```text
FCAPSule pod
  |-- API/UI container
  |-- worker process or queue consumer
  |-- mounted configuration
  |-- secret references
  |-- PostgreSQL metadata
  `-- object storage for derived capsules
       |
       +-- Alertmanager
       +-- Prometheus
       +-- OpenSearch
       +-- Kubernetes API
       `-- trace backend (query on demand)
```

Distributed workers and Kafka-triggered scheduling are future scaling work. They do not change the normalized case or capsule contracts.
