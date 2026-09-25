# FCAPSule Atlas Service

Atlas is a small, separately installable HTTP service for durable, scoped incident-case knowledge. It stores curated fact observations and unverified hypotheses in separate fields. It does not store raw telemetry or secret-bearing values, and it does not claim a cause or resolution.

## Run

Use PostgreSQL 14 or newer and set a connection URL and a long random bearer token (at least 24 bytes):

```sh
export DATABASE_URL='postgresql://atlas:password@localhost:5432/atlas'
export ATLAS_API_TOKEN="$(openssl rand -hex 32)"
python3 -m pip install -r atlas/requirements.txt
python3 -m uvicorn atlas.app:app --host 0.0.0.0 --port 8080
```

The service runs versioned SQL migrations at startup. `/healthz` is unauthenticated and checks database readiness; every `/v1/*` route requires `Authorization: Bearer $ATLAS_API_TOKEN`.

## API

- `POST /v1/cases` accepts a JSON case envelope with `schema_version: 1`, `instance_id`, `episode_id`, optional `revision` (defaults to `1`), `observed_at`, `scope`, `summary`, `observations`, `hypotheses`, and optional `fingerprint` / `normalization_version`. A new case returns `201 {"case": ..., "created": true}`. Replaying an identical `(instance_id, episode_id, revision)` returns `200` with the same case and `created: false`; a different payload under the same key returns `409`.
- `GET /v1/cases/{id}` returns `{"case": ...}`.
- `POST /v1/search` accepts optional `query`, `scope`, `instance_id`, `fingerprint`, `observed_after`, `before` (or `observed_before`), and `limit` (1-10). It returns `{ "cases": [...], "limit": N, "has_more": bool }`. Results are latest revisions per episode, scoped by exact supplied fields, and never newer than the requested cutoff. Exact fingerprints rank first; other results use basic token/phrase similarity with recent in-scope results as fallback. `score` is a ranking heuristic, not a probability.
- `GET /v1/patterns` accepts `scope` as a JSON query parameter (or direct scope fields), `query`, `before` / `observed_before`, and `limit` (1-50). It returns repeated typed observation aggregates and `has_more`.
- `GET /v1/patterns/{id}` returns the aggregate and up to 10 latest-revision member cases, with `has_more`; use `GET /v1/cases/{id}` for full case retrieval.

Scope supports `environment`, `cluster`, `namespace`, `service`, `workload`, `cnfc_id`, and `vnfc_id`. Pattern identity uses normalized observation `kind`, `key`, scalar `value`, and exact `unit`; unlike units are not converted. Pattern counts deduplicate revisions by episode and report distinct instances. A shared observation is reported only as observed co-occurrence, never as a proven shared cause.

Request bodies are capped at 40 KiB, with additional bounds on strings, observations, hypotheses, and result sizes. Observation values must be scalar. Secret-looking field names and values and nested/raw telemetry objects are rejected. This is defense in depth, not a substitute for upstream data minimization, access controls, TLS, backups, and secret management. Authentication currently uses one shared service token; it does not implement per-instance authorization.

## Tests

```sh
python3 -m pip install -r atlas/requirements-dev.txt
python3 -m unittest discover -s atlas/tests -v
```

The HTTP API tests use an in-memory repository. Set `ATLAS_TEST_DATABASE_URL` to a disposable PostgreSQL database to enable migration, idempotency, time-aware search, and revision-aware pattern integration tests.
