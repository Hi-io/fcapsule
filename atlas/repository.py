from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .normalize import observation_pattern_id, search_document


MIGRATIONS = Path(__file__).with_name("migrations")
MAX_SEARCH_CANDIDATES = 500
MAX_PATTERN_MEMBERS = 10
SCOPE_KEYS = ("environment", "cluster", "namespace", "service", "workload", "cnfc_id", "vnfc_id")
TOKEN_RE = re.compile(r"[a-z0-9_]+")


class IdempotencyConflict(ValueError):
    pass


def _utc(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def public_case(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "schema_version": row["schema_version"],
        "normalization_version": row["normalization_version"],
        "instance_id": row["instance_id"],
        "episode_id": row["episode_id"],
        "revision": row["revision"],
        "observed_at": _utc(row["observed_at"]),
        "scope": row["scope"],
        "summary": row["summary"],
        "observations": row["observations"],
        "hypotheses": row["hypotheses"],
        "fingerprint": row["fingerprint"],
    }


class PostgresAtlasRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get("DATABASE_URL", "")
        if not self.dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.dsn, connect_timeout=3, row_factory=dict_row)

    def migrate(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS atlas_schema_migrations "
                "(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            conn.execute("SELECT pg_advisory_xact_lock(620018271)")
            for path in sorted(MIGRATIONS.glob("*.sql")):
                version = int(path.name.split("_", 1)[0])
                existing = conn.execute(
                    "SELECT 1 FROM atlas_schema_migrations WHERE version = %s", (version,)
                ).fetchone()
                if existing:
                    continue
                for statement in path.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute("INSERT INTO atlas_schema_migrations (version) VALUES (%s)", (version,))

    def healthcheck(self) -> bool:
        with self._connect() as conn:
            conn.execute("SELECT 1 FROM atlas_schema_migrations LIMIT 1").fetchone()
        return True

    def create_case(self, case: dict[str, Any]) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        case_id = uuid.uuid4()
        document = search_document(case)
        values = (
            case_id,
            case["schema_version"],
            case["normalization_version"],
            case["instance_id"],
            case["episode_id"],
            case["revision"],
            case["observed_at"],
            Jsonb(case["scope"]),
            case["summary"],
            Jsonb(case["observations"]),
            Jsonb(case["hypotheses"]),
            case["fingerprint"],
            document,
        )
        with self._connect() as conn:
            inserted = conn.execute(
                """INSERT INTO atlas_cases
                   (id, schema_version, normalization_version, instance_id, episode_id, revision, observed_at,
                    scope, summary, observations, hypotheses, fingerprint, search_document)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (instance_id, episode_id, revision) DO NOTHING
                   RETURNING id""",
                values,
            ).fetchone()
            if inserted:
                unique_patterns: dict[str, dict[str, Any]] = {}
                for observation in case["observations"]:
                    pattern_id = observation_pattern_id(observation)
                    unique_patterns[pattern_id] = observation
                for pattern_id, observation in unique_patterns.items():
                    conn.execute(
                        """INSERT INTO atlas_case_patterns
                           (case_id, pattern_id, kind, key, value, unit)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (case_id, pattern_id, observation["kind"], observation["key"],
                         Jsonb(observation["value"]), observation["unit"]),
                    )
                return {"case": public_case({**case, "id": case_id}), "created": True}

            existing = conn.execute(
                """SELECT * FROM atlas_cases
                   WHERE instance_id = %s AND episode_id = %s AND revision = %s""",
                (case["instance_id"], case["episode_id"], case["revision"]),
            ).fetchone()
            existing_case = public_case(existing)
            if any(existing_case[key] != value for key, value in case.items()):
                raise IdempotencyConflict("This idempotency key already has a different case payload")
            return {"case": existing_case, "created": False}

    @staticmethod
    def _filters(
        scope: dict[str, str] | None = None,
        instance_id: str | None = None,
        observed_after: datetime | None = None,
        before: datetime | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if instance_id:
            clauses.append("c.instance_id = %s")
            params.append(instance_id)
        for key in SCOPE_KEYS:
            if scope and scope.get(key) is not None:
                clauses.append("c.scope ->> %s = %s")
                params.extend((key, scope[key]))
        if observed_after:
            clauses.append("c.observed_at >= %s")
            params.append(observed_after)
        if before:
            clauses.append("c.observed_at <= %s")
            params.append(before)
        return (" AND ".join(clauses) if clauses else "TRUE", params)

    @staticmethod
    def _latest_cte(where: str) -> str:
        return f"""WITH latest_cases AS (
            SELECT DISTINCT ON (c.instance_id, c.episode_id) c.*
            FROM atlas_cases c
            WHERE {where}
            ORDER BY c.instance_id, c.episode_id, c.revision DESC, c.observed_at DESC
        )"""

    def search(
        self,
        *,
        scope: dict[str, str] | None = None,
        query: str | None = None,
        fingerprint: str | None = None,
        instance_id: str | None = None,
        observed_after: datetime | None = None,
        before: datetime | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        where, params = self._filters(scope, instance_id, observed_after, before)
        terms = list(dict.fromkeys(TOKEN_RE.findall((query or "").casefold())))[:20]
        text_clause = ""
        if terms or fingerprint:
            parts: list[str] = []
            if fingerprint:
                parts.append("c.fingerprint = %s")
                params.append(fingerprint)
            if terms:
                parts.append("c.search_document ILIKE ANY(%s)")
                params.append([f"%{term}%" for term in terms])
            text_clause = f" AND ({' OR '.join(parts)})"
        order = "c.observed_at DESC"
        if fingerprint:
            order = "(c.fingerprint = %s) DESC, c.observed_at DESC"
            params.append(fingerprint)
        sql = self._latest_cte(where) + f"""
            SELECT c.* FROM latest_cases c
            WHERE TRUE{text_clause}
            ORDER BY {order}
            LIMIT %s
        """
        params.append(MAX_SEARCH_CANDIDATES)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        scored = []
        for row in rows:
            record = public_case(row)
            text = row["search_document"].casefold()
            exact_fingerprint = bool(fingerprint and row["fingerprint"] == fingerprint)
            matched = sum(1 for term in terms if term in text)
            coverage = matched / len(terms) if terms else 0.0
            phrase_bonus = 0.25 if query and " ".join(query.casefold().split()) in text else 0.0
            score = 1.0 if exact_fingerprint else min(1.0, coverage * 0.75 + phrase_bonus)
            relation = "fingerprint_match" if exact_fingerprint else (
                "lexical_similarity" if score > 0 else "recent_in_scope"
            )
            scored.append({**record, "score": round(score, 4), "relation": relation})
        scored.sort(key=lambda item: (item["score"], item["observed_at"] or ""), reverse=True)
        return {"cases": scored[:limit], "limit": limit, "has_more": len(scored) > limit}

    def list_patterns(
        self,
        *,
        scope: dict[str, str] | None = None,
        query: str | None = None,
        before: datetime | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        where, params = self._filters(scope, before=before)
        terms = list(dict.fromkeys(TOKEN_RE.findall((query or "").casefold())))[:20]
        match_clause = ""
        if terms:
            match_clause = " AND c.search_document ILIKE ANY(%s)"
            params.append([f"%{term}%" for term in terms])
        sql = self._latest_cte(where) + f"""
            SELECT p.pattern_id, p.kind, p.key, p.value, p.unit,
                   count(*) AS case_count,
                   count(DISTINCT c.instance_id) AS instance_count,
                   min(c.observed_at) AS first_seen,
                   max(c.observed_at) AS last_seen
            FROM latest_cases c
            JOIN atlas_case_patterns p ON p.case_id = c.id
            WHERE TRUE{match_clause}
            GROUP BY p.pattern_id, p.kind, p.key, p.value, p.unit
            HAVING count(*) >= 2
            ORDER BY case_count DESC, last_seen DESC, p.pattern_id
            LIMIT %s
        """
        params.append(limit + 1)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {
            "patterns": [self._public_pattern(row) for row in rows[:limit]],
            "limit": limit,
            "has_more": len(rows) > limit,
        }

    @staticmethod
    def _public_pattern(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["pattern_id"].strip(),
            "kind": row["kind"],
            "key": row["key"],
            "value": row["value"],
            "unit": row["unit"],
            "case_count": row["case_count"],
            "instance_count": row["instance_count"],
            "first_seen": _utc(row["first_seen"]),
            "last_seen": _utc(row["last_seen"]),
            "similarity": "same_observation",
            "interpretation": "Observed co-occurrence only; this is not evidence of a shared cause.",
        }

    def get_pattern(self, pattern_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9a-f]{24}", pattern_id):
            return None
        with self._connect() as conn:
            aggregate_sql = self._latest_cte("TRUE") + """
                SELECT p.pattern_id, p.kind, p.key, p.value, p.unit,
                       count(*) AS case_count,
                       count(DISTINCT c.instance_id) AS instance_count,
                       min(c.observed_at) AS first_seen,
                       max(c.observed_at) AS last_seen
                FROM latest_cases c JOIN atlas_case_patterns p ON c.id = p.case_id
                WHERE p.pattern_id = %s
                GROUP BY p.pattern_id, p.kind, p.key, p.value, p.unit
            """
            aggregate = conn.execute(
                aggregate_sql,
                (pattern_id,),
            ).fetchone()
            if not aggregate:
                return None
            rows = conn.execute(
                self._latest_cte("TRUE") + """
                    SELECT c.* FROM latest_cases c
                    JOIN atlas_case_patterns p ON p.case_id = c.id
                    WHERE p.pattern_id = %s
                    ORDER BY c.observed_at DESC
                    LIMIT %s
                """,
                (pattern_id, MAX_PATTERN_MEMBERS + 1),
            ).fetchall()
        return {
            "pattern": self._public_pattern(aggregate),
            "cases": [public_case(row) for row in rows[:MAX_PATTERN_MEMBERS]],
            "has_more": len(rows) > MAX_PATTERN_MEMBERS,
        }

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        try:
            parsed = uuid.UUID(case_id)
        except (ValueError, AttributeError):
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM atlas_cases WHERE id = %s", (parsed,)).fetchone()
        return None if row is None else public_case(row)
