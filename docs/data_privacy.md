# Data Privacy and Retention

## Collection Boundary

FCAPSule queries only the application and bounded time window required for an incident. Source credentials should be read-only and scoped to the minimum required indices, queries, and namespaces.

## Stored Data

FCAPSule stores:

- application and incident metadata;
- FM alert context;
- ranked evidence and score explanations;
- PM anomaly summaries;
- anonymized representative log lines;
- topology/configuration facts relevant to the incident;
- trace-source availability and derived findings;
- grounded hypotheses, next checks, and evaluation;
- model prompts and outputs when comparison is explicitly enabled.

FCAPSule does not store:

- unrestricted raw log collections;
- complete PM exports in capsule archives;
- raw distributed trace spans;
- provider API keys;
- passwords or observability credentials;
- remediation commands.

## Anonymization

Before representative log lines enter evidence, processing masks:

- IP addresses;
- email addresses;
- UUID-shaped identifiers;
- long hexadecimal identifiers;
- token, secret, and password assignments;
- variable numeric values used for template grouping.

Adapters should add organization-specific field removal before normalization when required.

## Trace Policy

Trace systems remain the source of truth. FCAPSule may probe or query them while the source retention window is active. It retains availability, timing, derived evidence, and source references. It does not copy the raw span set.

## Model Boundary

External models receive the selected capsule evidence, not unrestricted source telemetry. Every response is recorded with provider/model metadata and checked for valid evidence citations.

## Local Secrets and State

`.env` and `.fcapsule/` are ignored by Git. Production deployments should use a secret manager and encrypted durable storage with explicit retention, access control, and audit policies.

