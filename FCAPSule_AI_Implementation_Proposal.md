# FCAPSule: Implementation and Next Decisions

**Status:** current reference design plus explicitly proposed changes. The [concept](FCAPSule_AI_Concept.md) describes the stable goal; [PROJECT_DESIGN](PROJECT_DESIGN.md), [architecture](docs/architecture.md) and [AI techniques](docs/ai_investigation_techniques.md) are the detailed implementation references. The [earlier proposal](docs/history/initial_implementation_proposal.md) is preserved for the project history.

## Implemented Reference Path

1. A firing Prometheus alert or an imported normalized case defines a bounded incident window. Kubernetes discovery aligns the workload identity with Prometheus labels and OpenSearch log documents.
2. Source adapters collect bounded fault, performance, log and supported Kubernetes configuration observations. Live normalized inputs are staged under managed state and become eligible for independent cleanup after a configurable TTL (24 hours by default); the source systems remain authoritative.
3. A deterministic pipeline groups log patterns, analyzes metric changes, selects evidence across domains and writes an incident report and capsule. This path does not require a model key.
4. With a core provider configured, a background episode investigator selects from read-only diagnostic tools, records observations, compares plausible explanations and publishes a cited assessment. A review pass checks the draft against visible evidence. Valid IDs and schema are enforced, but semantic correctness still needs human review.
5. Retained recurrence candidates can supply one bounded earlier capsule for comparison. The previous model answer is a hypothesis, while captured observations remain evidence.
6. Optional vision and speech specialists process operator-supplied evidence after capability checks. The engineer can request a reviewed reassessment; media is never collected automatically.
7. Operations presents the queue and report; Targets owns connections and coverage; Patterns exposes recurrence; Settings owns retention and model configuration. CLI and HTTP interfaces remain available without the UI.

SQLite, a local artifact directory/PVC, a standard-library HTTP server and background threads implement the single-replica reference. Derived ZIP exports exclude staged raw inputs, but selected examples and metric values can still be sensitive. See [privacy and retention](docs/data_privacy.md).

## Important Design Contracts

- Keep original alerts and incident-specific reports even when episodes correlate signals.
- Show the actual affected resource and evidence time; distinguish incident-time facts from a later live check.
- Bound queries, prompt input, completed checks and optional media calls. Preserve a usable deterministic report when an AI request fails.
- Treat citations as navigation to retained observations, not as proof of a diagnosis.
- Keep the simulator and scenario oracle in the [separate Lab](https://github.com/Hi-io/fcapsule-lab). The product must not receive injected causes as an answer key.
- Preserve capsule access after source expiry, but never claim to recover data that was not captured.

## Proposed Work, Not Shipped Capability

| Decision area | Direction and required proof |
| --- | --- |
| Production access controls | Add authentication, authorization, TLS integration and auditability before exposing the reference service outside a trusted boundary. |
| Capture lifecycle | Consider an independently configurable short TTL for staged raw inputs; verify that later report reading and retained-only review still work. |
| Reliability and scale | Replace in-process jobs and single-replica SQLite/file ownership only when durability, load and recovery tests justify a queue and shared stores. |
| Source breadth | Add a trace backend or new observability source only with bounded queries, provenance, access controls and evidence of operator value. |
| Diagnosis quality | Evaluate retrieval, cross-workload matching and model/tool changes on held-out incidents. Keep failures and inconclusive results in the record. |

The [roadmap](ROADMAP.md) tracks the broader sequence. A production-looking UI or a strong synthetic demo does not itself establish telecom readiness, diagnosis accuracy or a measured retention-cost reduction.
