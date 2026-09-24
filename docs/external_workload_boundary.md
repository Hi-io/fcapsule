# External Workload Boundary

## Decision

FCAPSule is an incident evidence product, not a workload generator, traffic tool,
container orchestrator, database simulator, or Prometheus replacement. A user operates
their applications and observability stack independently. FCAPSule observes the bounded
incident context, preserves the selected evidence, and produces an investigation report.

Workloads used for demonstrations and adapter validation live in the separate `fcapsule-lab` repository. It owns Compose/Kubernetes deployments, databases, traffic and failure controls. Its current inventory is defined in that repository, not in the FCAPSule product contract. No failure controls appear in the operator console.

## User Value

An SRE needs FCAPSule to answer operational questions quickly:

- Which incident needs attention and which application is affected?
- What user impact and alert sequence established the incident window?
- Which PM trend and log patterns support the likely investigation path?
- What should be preserved or checked before source retention expires?
- Where is the derived incident package, and can it be exported?

The Operations console is designed around those questions. Engineering quality metrics,
model comparisons, and simulator controls are intentionally absent from the primary
workflow. Deterministic evidence selection creates the report first; the optional cited
AI briefing provides a second reading only after that evidence is retained.

## Integration Contract

One hand-off is a normalized case directory. The producer owns collection from its
sources and writes `metadata.yaml`, `alert.json`, `prometheus_metrics.json`, and
`opensearch_logs.json` for one bounded window. FCAPSule validates that contract and
records the incident with `fcapsule ingest-case`. It does not copy source retention
systems or keep a permanent raw log/trace mirror. The other path is the built-in live-source integration, which stages bounded inputs on the state volume until incident retention/deletion.

External exporters can query independent sources and structured stdout logs. In Kubernetes, the implemented live
adapters perform read-only bounded queries against Prometheus and OpenSearch and resolve
pods plus referenced ConfigMaps through the Kubernetes API. Both paths preserve the same normalized contract.

## AI Configuration Boundary

The AI model is a local optional enrichment setting. `.fcapsule/ai-settings.json` holds
the selected provider, model ID, and completion budget; `.env` holds provider secrets
and is Git-ignored. The console reports only whether a key is configured. It never
returns, renders, archives, or stores a key in SQLite. Core investigation supports the
DeepSeek and OpenRouter adapters; another provider requires an explicit adapter, not
merely an unverified model name. The optional OpenRouter media specialists have
separate capability validation from the core investigator.

## Open-source Readiness

The separation makes each repository independently useful and testable. FCAPSule can be
deployed beside real observability systems without shipping demo services. FCAPSule Lab
can evolve as a reproducible adapter fixture without acquiring product state or access to
production credentials. Future work can add other source adapters, authentication,
durable object storage, and distributed execution without altering this boundary.
