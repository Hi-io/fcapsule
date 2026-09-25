CREATE TABLE atlas_cases (
    id uuid PRIMARY KEY,
    schema_version smallint NOT NULL CHECK (schema_version = 1),
    normalization_version text,
    instance_id text NOT NULL,
    episode_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    observed_at timestamptz NOT NULL,
    scope jsonb NOT NULL,
    summary text NOT NULL,
    observations jsonb NOT NULL,
    hypotheses jsonb NOT NULL,
    fingerprint text,
    search_document text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (instance_id, episode_id, revision)
);

CREATE INDEX atlas_cases_observed_at_idx ON atlas_cases (observed_at DESC);
CREATE INDEX atlas_cases_fingerprint_idx ON atlas_cases (fingerprint) WHERE fingerprint IS NOT NULL;
CREATE INDEX atlas_cases_scope_idx ON atlas_cases USING gin (scope);
CREATE INDEX atlas_cases_search_idx ON atlas_cases USING gin (to_tsvector('simple', search_document));

CREATE TABLE atlas_case_patterns (
    case_id uuid NOT NULL REFERENCES atlas_cases(id) ON DELETE CASCADE,
    pattern_id char(24) NOT NULL,
    kind text NOT NULL,
    key text NOT NULL,
    value jsonb NOT NULL,
    unit text,
    PRIMARY KEY (case_id, pattern_id)
);

CREATE INDEX atlas_case_patterns_id_idx ON atlas_case_patterns (pattern_id, case_id);
