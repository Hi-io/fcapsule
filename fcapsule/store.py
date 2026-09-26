"""Persistent control-plane metadata for FCAPSule.

Raw telemetry remains in the configured observability systems. This store keeps
application registrations, incident/capsule metadata, and model preferences.
"""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any


DEFAULT_MODEL_PROFILES = (
    ("deepseek-v4-flash", "deepseek", 2400, False),
    ("deepseek-v4-pro", "deepseek", 3600, True),
    ("deepseek/deepseek-v4-flash-0731", "openrouter", 2400, False),
    ("deepseek/deepseek-v4-pro-0813", "openrouter", 3600, True),
)

EPISODE_JOIN_MINUTES = 15
EPISODE_RESOURCE_CORRELATION_MINUTES = 2
MAX_PUBLICATION_REVISION_LINKS = 32
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite, then release the database handle promptly."""

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


def _decode(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _reference(prefix: str, value: str) -> str:
    """Provide a stable human-sized reference without replacing source IDs."""

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8].upper()
    return f"{prefix}-{digest}"


def _identity(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _recurrence_key(app_id: str, resource_kind: str, resource_name: str, alert_identity: str) -> str:
    values = (app_id, resource_kind, resource_name, alert_identity)
    return "|".join(_identity(value) for value in values)


def _alert_family(recurrence_key: str) -> str:
    """Return the alert signature independent of the affected pod/resource."""

    parts = recurrence_key.split("|", 3)
    return parts[3] if len(parts) == 4 else ""


class FCAPSuleStore:
    """Small SQLite repository used by the CLI and local control plane."""

    def __init__(self, path: str | Path = ".fcapsule/fcapsule.db") -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    app_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_config TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL REFERENCES applications(app_id),
                    scenario TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    case_dir TEXT NOT NULL,
                    alert_count INTEGER NOT NULL,
                    log_count INTEGER NOT NULL,
                    metric_series_count INTEGER NOT NULL,
                    raw_bytes INTEGER NOT NULL,
                    trace_access TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    archived_at TEXT,
                    source_kind TEXT NOT NULL DEFAULT 'external',
                    resource_kind TEXT NOT NULL DEFAULT 'application',
                    resource_name TEXT NOT NULL DEFAULT '',
                    recurrence_key TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS incident_episodes (
                    episode_id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL REFERENCES applications(app_id),
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    last_activity_at TEXT NOT NULL,
                    ended_at TEXT,
                    primary_incident_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT,
                    resource_kind TEXT NOT NULL DEFAULT 'application',
                    resource_name TEXT NOT NULL DEFAULT '',
                    recurrence_key TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS episode_incidents (
                    episode_id TEXT NOT NULL REFERENCES incident_episodes(episode_id) ON DELETE CASCADE,
                    incident_id TEXT NOT NULL UNIQUE REFERENCES incidents(incident_id) ON DELETE CASCADE,
                    PRIMARY KEY (episode_id, incident_id)
                );

                CREATE TABLE IF NOT EXISTS capsules (
                    capsule_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
                    app_id TEXT NOT NULL REFERENCES applications(app_id),
                    status TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    archive_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    selected_evidence INTEGER NOT NULL,
                    compression REAL NOT NULL,
                    signal_preservation REAL NOT NULL,
                    grounding REAL NOT NULL,
                    runtime_seconds REAL NOT NULL,
                    model_winner TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_profiles (
                    model_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    max_tokens INTEGER NOT NULL,
                    enabled INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidence_attachments (
                    attachment_id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL REFERENCES incident_episodes(episode_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    observed_at TEXT,
                    context_note TEXT NOT NULL DEFAULT '',
                    source_redacted INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    extraction TEXT NOT NULL DEFAULT '{}',
                    correction TEXT NOT NULL DEFAULT '',
                    provider TEXT,
                    model TEXT,
                    usage TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS investigation_revisions (
                    revision_id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL REFERENCES incident_episodes(episode_id) ON DELETE CASCADE,
                    parent_revision_id TEXT,
                    reason TEXT NOT NULL,
                    source_mode TEXT NOT NULL DEFAULT 'live_sources',
                    input_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_manifest TEXT NOT NULL DEFAULT '[]',
                    state_path TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS source_disconnected_reviews (
                    review_id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL REFERENCES incident_episodes(episode_id) ON DELETE CASCADE,
                    question TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state_path TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}',
                    usage TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS related_episode_groups (
                    group_id TEXT PRIMARY KEY,
                    correlation_key TEXT NOT NULL UNIQUE,
                    cluster TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    basis TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS related_group_members (
                    group_id TEXT NOT NULL REFERENCES related_episode_groups(group_id) ON DELETE CASCADE,
                    episode_id TEXT NOT NULL REFERENCES incident_episodes(episode_id) ON DELETE CASCADE,
                    PRIMARY KEY (group_id, episode_id)
                );

                CREATE TABLE IF NOT EXISTS related_group_exclusions (
                    correlation_key TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (correlation_key, episode_id)
                );

                CREATE TABLE IF NOT EXISTS atlas_outbox (
                    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    local_episode_id TEXT NOT NULL DEFAULT '',
                    local_episode_link_status TEXT NOT NULL DEFAULT 'unknown',
                    revision INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT,
                    investigation_revision_ids TEXT NOT NULL DEFAULT '[]',
                    investigation_revision_links_truncated INTEGER NOT NULL DEFAULT 0,
                    remote_case_id TEXT,
                    receipt_status TEXT NOT NULL DEFAULT 'legacy_receipt_unknown',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(instance_id, episode_id, payload_digest)
                );

                CREATE TABLE IF NOT EXISTS atlas_publication_heads (
                    instance_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (instance_id, episode_id)
                );

                CREATE INDEX IF NOT EXISTS idx_incidents_app_time
                    ON incidents(app_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_capsules_app_time
                    ON capsules(app_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_episodes_app_activity
                    ON incident_episodes(app_id, last_activity_at DESC);
                CREATE INDEX IF NOT EXISTS idx_episode_incidents_episode
                    ON episode_incidents(episode_id);
                CREATE INDEX IF NOT EXISTS idx_evidence_attachments_episode
                    ON evidence_attachments(episode_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_investigation_revisions_episode
                    ON investigation_revisions(episode_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_source_disconnected_reviews_episode
                    ON source_disconnected_reviews(episode_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_related_group_members_episode
                    ON related_group_members(episode_id);
                CREATE INDEX IF NOT EXISTS idx_atlas_outbox_due
                    ON atlas_outbox(status, next_attempt_at, created_at);
                """
            )
            outbox_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(atlas_outbox)").fetchall()
            }
            for column, definition in (
                ("local_episode_id", "TEXT NOT NULL DEFAULT ''"),
                ("local_episode_link_status", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("investigation_revision_ids", "TEXT NOT NULL DEFAULT '[]'"),
                ("investigation_revision_links_truncated", "INTEGER NOT NULL DEFAULT 0"),
                ("remote_case_id", "TEXT"),
                ("receipt_status", "TEXT NOT NULL DEFAULT 'legacy_receipt_unknown'"),
            ):
                if column not in outbox_columns:
                    connection.execute(f"ALTER TABLE atlas_outbox ADD COLUMN {column} {definition}")
            incident_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(incidents)").fetchall()
            }
            if "archived_at" not in incident_columns:
                connection.execute("ALTER TABLE incidents ADD COLUMN archived_at TEXT")
            if "source_kind" not in incident_columns:
                connection.execute("ALTER TABLE incidents ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'external'")
            for column, definition in (
                ("resource_kind", "TEXT NOT NULL DEFAULT 'application'"),
                ("resource_name", "TEXT NOT NULL DEFAULT ''"),
                ("recurrence_key", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in incident_columns:
                    connection.execute(f"ALTER TABLE incidents ADD COLUMN {column} {definition}")
            episode_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(incident_episodes)").fetchall()
            }
            for column, definition in (
                ("resource_kind", "TEXT NOT NULL DEFAULT 'application'"),
                ("resource_name", "TEXT NOT NULL DEFAULT ''"),
                ("recurrence_key", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in episode_columns:
                    connection.execute(f"ALTER TABLE incident_episodes ADD COLUMN {column} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_recurrence "
                "ON incident_episodes(recurrence_key, started_at DESC)"
            )
            connection.execute(
                "UPDATE incidents SET source_kind = 'live' WHERE case_dir LIKE ?",
                ("%/live-cases/%",),
            )
            self._backfill_episodes(connection)
            self._backfill_incident_identity(connection)
            self._backfill_episode_identity(connection)
            now = utc_now()
            for model_id, provider, max_tokens, enabled in DEFAULT_MODEL_PROFILES:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO model_profiles
                        (model_id, provider, max_tokens, enabled, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (model_id, provider, max_tokens, int(enabled), now),
                )

    def upsert_application(
        self,
        app_id: str,
        name: str,
        namespace: str,
        cluster: str,
        environment: str = "development",
        status: str = "healthy",
        source_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        config = source_config or {
            "faults": {"adapter": "file"},
            "metrics": {"adapter": "file"},
            "logs": {"adapter": "file"},
            "traces": {"adapter": "on_demand", "retain_raw_spans": False},
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO applications
                    (app_id, name, namespace, cluster, environment, status, source_config, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(app_id) DO UPDATE SET
                    name=excluded.name,
                    namespace=excluded.namespace,
                    cluster=excluded.cluster,
                    environment=excluded.environment,
                    status=excluded.status,
                    source_config=excluded.source_config,
                    updated_at=excluded.updated_at
                """,
                (app_id, name, namespace, cluster, environment, status, _json(config), now, now),
            )
        return self.get_application(app_id) or {}

    def set_application_status(self, app_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE applications SET status = ?, updated_at = ? WHERE app_id = ?",
                (status, utc_now(), app_id),
            )

    def get_application(self, app_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM applications WHERE app_id = ?", (app_id,)).fetchone()
        return self._application_row(row) if row else None

    def list_applications(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*,
                       (SELECT COUNT(*) FROM incidents i WHERE i.app_id = a.app_id) AS incident_count,
                       (SELECT COUNT(*) FROM capsules c WHERE c.app_id = a.app_id) AS capsule_count,
                       (SELECT MAX(i.started_at) FROM incidents i WHERE i.app_id = a.app_id) AS last_incident_at
                FROM applications a
                ORDER BY a.name
                """
            ).fetchall()
        return [self._application_row(row) for row in rows]

    @staticmethod
    def _application_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["source_config"] = _decode(result.get("source_config"), {})
        return result

    def record_incident(self, payload: dict[str, Any]) -> dict[str, Any]:
        incident_id = str(payload["incident_id"])
        resource_kind = str(payload.get("resource_kind") or "application")
        resource_name = str(payload.get("resource_name") or payload["app_id"])
        recurrence_key = str(payload.get("recurrence_key") or _recurrence_key(
            str(payload["app_id"]), resource_kind, resource_name,
            str(payload.get("alert_identity") or payload.get("summary") or payload.get("scenario", "unknown")),
        ))
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents
                    (incident_id, app_id, scenario, status, severity, started_at, ended_at,
                     case_dir, alert_count, log_count, metric_series_count, raw_bytes,
                     trace_access, summary, created_at, source_kind, resource_kind, resource_name, recurrence_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    status=excluded.status,
                    ended_at=excluded.ended_at,
                    alert_count=excluded.alert_count,
                    log_count=excluded.log_count,
                    metric_series_count=excluded.metric_series_count,
                    raw_bytes=excluded.raw_bytes,
                    trace_access=excluded.trace_access,
                    summary=excluded.summary,
                    source_kind=excluded.source_kind,
                    resource_kind=excluded.resource_kind,
                    resource_name=excluded.resource_name,
                    recurrence_key=excluded.recurrence_key
                """,
                (
                    incident_id,
                    payload["app_id"],
                    payload.get("scenario", "unknown"),
                    payload.get("status", "firing"),
                    payload.get("severity", "warning"),
                    payload["started_at"],
                    payload.get("ended_at"),
                    str(payload["case_dir"]),
                    int(payload.get("alert_count", 0)),
                    int(payload.get("log_count", 0)),
                    int(payload.get("metric_series_count", 0)),
                    int(payload.get("raw_bytes", 0)),
                    _json(payload.get("trace_access", {})),
                    payload.get("summary", ""),
                    now,
                    payload.get("source_kind", "external"),
                    resource_kind,
                    resource_name,
                    recurrence_key,
                ),
            )
            if str(payload.get("status", "firing")).lower() != "pending":
                self._assign_episode(connection, incident_id, observed_at=now)
            connection.execute(
                "UPDATE applications SET status = ?, updated_at = ? WHERE app_id = ?",
                ("degraded", now, payload["app_id"]),
            )
        return self.get_incident(incident_id) or {}

    def register_imported_capsule(
        self,
        application: dict[str, Any],
        incident: dict[str, Any],
        capsule: dict[str, Any],
    ) -> dict[str, Any]:
        """Register a retained archive atomically without upserting or changing app health."""
        now = utc_now()
        app_id = str(application["app_id"])
        incident_id = str(incident["incident_id"])
        capsule_id = str(capsule["capsule_id"])
        resource_kind = str(incident.get("resource_kind") or "application")
        resource_name = str(incident.get("resource_name") or application["name"])
        recurrence_key = _recurrence_key(
            app_id, resource_kind, resource_name,
            str(incident.get("summary") or incident.get("scenario") or "imported capsule"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO applications
                    (app_id, name, namespace, cluster, environment, status, source_config, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'unknown', ?, ?, ?)
                """,
                (
                    app_id, application["name"], application["namespace"], application["cluster"],
                    "imported", _json({"source": "capsule_archive_import", "live_capture": False}), now, now,
                ),
            )
            connection.execute(
                """
                INSERT INTO incidents
                    (incident_id, app_id, scenario, status, severity, started_at, ended_at,
                     case_dir, alert_count, log_count, metric_series_count, raw_bytes,
                     trace_access, summary, created_at, source_kind, resource_kind, resource_name, recurrence_key)
                VALUES (?, ?, ?, 'resolved', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 'imported', ?, ?, ?)
                """,
                (
                    incident_id, app_id, incident.get("scenario", "Imported capsule"),
                    incident.get("severity", "warning"), incident["started_at"], incident.get("ended_at"),
                    str(incident["case_dir"]), int(incident.get("alert_count", 0)),
                    int(incident.get("log_count", 0)), int(incident.get("metric_series_count", 0)),
                    _json(incident.get("trace_access", {})), incident.get("summary", ""), now,
                    resource_kind, resource_name, recurrence_key,
                ),
            )
            self._assign_episode(connection, incident_id, observed_at=now)
            connection.execute(
                """
                INSERT INTO capsules
                    (capsule_id, incident_id, app_id, status, output_dir, archive_path,
                     size_bytes, selected_evidence, compression, signal_preservation,
                     grounding, runtime_seconds, model_winner, created_at)
                VALUES (?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    capsule_id, incident_id, app_id, str(capsule["output_dir"]), str(capsule["archive_path"]),
                    int(capsule.get("size_bytes", 0)), int(capsule.get("selected_evidence", 0)),
                    float(capsule.get("compression", 0)), float(capsule.get("signal_preservation", 0)),
                    float(capsule.get("grounding", 0)), float(capsule.get("runtime_seconds", 0)), now,
                ),
            )
        return {
            "application": self.get_application(app_id) or {},
            "incident": self.get_incident(incident_id) or {},
            "capsule": self.get_capsule(capsule_id) or {},
            "episode": self.episode_for_incident(incident_id) or {},
        }

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _backfill_episodes(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT i.incident_id
            FROM incidents i
            LEFT JOIN episode_incidents ei ON ei.incident_id = i.incident_id
            WHERE ei.incident_id IS NULL AND lower(i.status) != 'pending'
            ORDER BY i.started_at, i.created_at
            """
        ).fetchall()
        for row in rows:
            self._assign_episode(connection, str(row["incident_id"]))

    def _backfill_incident_identity(self, connection: sqlite3.Connection) -> None:
        """Give imported pre-identity records a conservative application identity."""

        rows = connection.execute(
            """
            SELECT i.*, a.name AS application_name
            FROM incidents i JOIN applications a ON a.app_id = i.app_id
            WHERE i.recurrence_key = '' OR i.resource_name = ''
            """
        ).fetchall()
        for row in rows:
            resource_kind = str(row["resource_kind"] or "application")
            resource_name = str(row["resource_name"] or row["application_name"] or row["app_id"])
            recurrence_key = str(row["recurrence_key"] or _recurrence_key(
                str(row["app_id"]), resource_kind, resource_name, str(row["summary"] or row["scenario"])
            ))
            connection.execute(
                "UPDATE incidents SET resource_kind = ?, resource_name = ?, recurrence_key = ? WHERE incident_id = ?",
                (resource_kind, resource_name, recurrence_key, row["incident_id"]),
            )

    def _backfill_episode_identity(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT episode_id FROM incident_episodes").fetchall()
        for row in rows:
            self._refresh_episode(connection, str(row["episode_id"]))

    def _assign_episode(
        self, connection: sqlite3.Connection, incident_id: str, observed_at: str | None = None
    ) -> str:
        linked = connection.execute(
            "SELECT episode_id FROM episode_incidents WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if linked:
            episode_id = str(linked["episode_id"])
            self._refresh_episode(connection, episode_id, observed_at=observed_at)
            return episode_id

        incident = connection.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
        if not incident:
            raise KeyError(f"Unknown incident: {incident_id}")
        started_at = str(incident["started_at"])
        started = self._parse_time(started_at)
        lower_bound = (started - timedelta(minutes=EPISODE_JOIN_MINUTES)).isoformat().replace("+00:00", "Z")
        upper_bound = (started + timedelta(minutes=EPISODE_JOIN_MINUTES)).isoformat().replace("+00:00", "Z")
        lower_started = self._parse_time(lower_bound)
        upper_started = self._parse_time(upper_bound)
        incident_family = _alert_family(str(incident["recurrence_key"] or ""))
        # Keep normal joins inside the episode's original window; only an overlapping firing copy of the same alert can extend it.
        candidate_members = connection.execute(
            """
            SELECT episode.episode_id, episode.started_at AS episode_started_at,
                   episode.last_activity_at, member.started_at, member.ended_at,
                   member.status, member.resource_kind, member.resource_name, member.recurrence_key
            FROM incident_episodes episode
            JOIN episode_incidents membership ON membership.episode_id = episode.episode_id
            JOIN incidents member ON member.incident_id = membership.incident_id
            WHERE episode.app_id = ? AND episode.archived_at IS NULL
              AND (
                  (episode.started_at >= ? AND episode.started_at <= ?
                   AND member.started_at >= ? AND member.started_at <= ?)
                  OR (? != '' AND lower(member.status) = 'firing' AND member.ended_at IS NULL
                      AND substr(member.recurrence_key, -length(?)) = ?)
              )
            ORDER BY episode.last_activity_at DESC
            """,
            (
                incident["app_id"], lower_bound, upper_bound, lower_bound, upper_bound,
                incident_family, f"|{incident_family}", f"|{incident_family}",
            ),
        ).fetchall()
        same_family = []
        same_resource = []
        correlation_bound = timedelta(minutes=EPISODE_RESOURCE_CORRELATION_MINUTES)
        current_end = (
            self._parse_time(str(incident["ended_at"]))
            if incident["ended_at"]
            else None if str(incident["status"]).lower() == "firing" else started
        )
        for member in candidate_members:
            episode_id = str(member["episode_id"])
            episode_started = self._parse_time(str(member["episode_started_at"]))
            member_started = self._parse_time(str(member["started_at"]))
            distance = abs(started - member_started)
            member_family = _alert_family(str(member["recurrence_key"] or ""))
            episode_is_in_window = lower_started <= episode_started <= upper_started
            member_is_in_window = lower_started <= member_started <= upper_started
            member_end = (
                self._parse_time(str(member["ended_at"]))
                if member["ended_at"]
                else None if str(member["status"]).lower() == "firing" else member_started
            )
            intervals_overlap = (current_end is None or member_started <= current_end) and (
                member_end is None or started <= member_end
            ) and member_started <= started
            if incident_family and member_family == incident_family and (
                (episode_is_in_window and member_is_in_window) or intervals_overlap
            ):
                same_family.append((distance, str(member["last_activity_at"]), episode_id))
            elif (
                episode_is_in_window
                and member_is_in_window
                and distance <= correlation_bound
                and str(member["resource_kind"]) == str(incident["resource_kind"])
                and str(member["resource_name"]) == str(incident["resource_name"])
            ):
                same_resource.append((distance, str(member["last_activity_at"]), episode_id))
        candidates = same_family or same_resource
        candidate = min(candidates, key=lambda item: (item[0], -self._parse_time(item[1]).timestamp())) if candidates else None
        if candidate:
            episode_id = candidate[2]
        else:
            episode_id = f"episode-{incident_id}"
            now = utc_now()
            connection.execute(
                """
                INSERT INTO incident_episodes
                    (episode_id, app_id, title, status, severity, started_at, last_activity_at,
                     ended_at, primary_incident_id, created_at, updated_at,
                     resource_kind, resource_name, recurrence_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    incident["app_id"],
                    incident["summary"] or incident["scenario"],
                    "active" if str(incident["status"]).lower() == "firing" else "resolved",
                    incident["severity"],
                    observed_at or str(incident["created_at"]),
                    started_at,
                    None if str(incident["status"]).lower() == "firing" else incident["ended_at"],
                    incident_id,
                    now,
                    now,
                    incident["resource_kind"],
                    incident["resource_name"],
                    incident["recurrence_key"],
                ),
            )
        connection.execute(
            "INSERT OR IGNORE INTO episode_incidents (episode_id, incident_id) VALUES (?, ?)",
            (episode_id, incident_id),
        )
        self._refresh_episode(connection, episode_id, observed_at=observed_at)
        return episode_id

    def _refresh_episode(
        self, connection: sqlite3.Connection, episode_id: str, observed_at: str | None = None
    ) -> None:
        signals = connection.execute(
            """
            SELECT i.* FROM incidents i
            JOIN episode_incidents ei ON ei.incident_id = i.incident_id
            WHERE ei.episode_id = ?
            ORDER BY i.started_at, i.created_at
            """,
            (episode_id,),
        ).fetchall()
        if not signals:
            connection.execute("DELETE FROM incident_episodes WHERE episode_id = ?", (episode_id,))
            return
        firing = [item for item in signals if str(item["status"]).lower() == "firing"]
        primary = max(
            firing or signals,
            key=lambda item: (SEVERITY_RANK.get(str(item["severity"]).lower(), 0), str(item["started_at"])),
        )
        active = bool(firing)
        ended_values = [str(item["ended_at"]) for item in signals if item["ended_at"]]
        activity_values = [str(item["created_at"]) for item in signals]
        if observed_at:
            activity_values.append(observed_at)
        connection.execute(
            """
            UPDATE incident_episodes
            SET title = ?, status = ?, severity = ?, started_at = ?, last_activity_at = ?,
                ended_at = ?, primary_incident_id = ?, updated_at = ?, resource_kind = ?,
                resource_name = ?, recurrence_key = ?
            WHERE episode_id = ?
            """,
            (
                primary["summary"] or primary["scenario"],
                "active" if active else "resolved",
                primary["severity"],
                min(str(item["started_at"]) for item in signals),
                max(activity_values),
                None if active else (max(ended_values) if ended_values else max(str(item["started_at"]) for item in signals)),
                primary["incident_id"],
                utc_now(),
                primary["resource_kind"],
                primary["resource_name"],
                primary["recurrence_key"],
                episode_id,
            ),
        )

    def list_episodes(self, limit: int = 50, archived: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM incident_episodes WHERE archived_at IS "
                + ("NOT NULL" if archived else "NULL")
                + " ORDER BY last_activity_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._episode_row(connection, row) for row in rows]

    def episode_for_incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT episode_id FROM episode_incidents WHERE incident_id = ?", (incident_id,)).fetchone()
        return self.get_episode(row["episode_id"]) if row else None

    def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM incident_episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
            return self._episode_row(connection, row) if row else None

    def _episode_row(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["reference"] = _reference("EP", str(row["episode_id"]))
        result["resource"] = {
            "kind": str(row["resource_kind"] or "application"),
            "name": str(row["resource_name"] or ""),
        }
        signal_rows = connection.execute(
            """
            SELECT i.*, CASE WHEN c.capsule_id IS NULL THEN 0 ELSE 1 END AS report_ready
            FROM incidents i
            JOIN episode_incidents ei ON ei.incident_id = i.incident_id
            LEFT JOIN capsules c ON c.incident_id = i.incident_id
            WHERE ei.episode_id = ?
            GROUP BY i.incident_id
            ORDER BY i.started_at
            """,
            (row["episode_id"],),
        ).fetchall()
        signals = [self._incident_row(item) for item in signal_rows]
        result["signals"] = signals
        result["signal_count"] = len(signals)
        result["report_count"] = sum(int(item.get("report_ready", 0)) for item in signals)
        result["alert_count"] = sum(int(item["alert_count"]) for item in signals)
        result["recurrence"] = self._recurrence_summary(connection, row)
        return result

    def recurrence_candidates_for_incident(self, episode_id: str, incident_id: str) -> list[dict[str, Any]]:
        """Find earlier exact-target episodes, then live same-workload pod recurrences."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT e.episode_id, e.started_at, e.app_id, i.recurrence_key,
                       i.source_kind, i.resource_kind, i.resource_name,
                       a.name AS workload_name, a.namespace, a.cluster
                FROM incident_episodes e
                JOIN episode_incidents ei ON ei.episode_id = e.episode_id
                JOIN incidents i ON i.incident_id = ei.incident_id
                JOIN applications a ON a.app_id = e.app_id
                WHERE e.episode_id = ? AND i.incident_id = ? AND i.app_id = e.app_id
                """,
                (episode_id, incident_id),
            ).fetchone()
            if not row or not row["recurrence_key"]:
                return []
            exact = connection.execute(
                """
                SELECT DISTINCT e.episode_id, e.title, e.started_at, e.ended_at, e.status, e.severity
                FROM incident_episodes e
                JOIN episode_incidents ei ON ei.episode_id = e.episode_id
                JOIN incidents i ON i.incident_id = ei.incident_id
                WHERE e.app_id = ? AND i.app_id = e.app_id AND i.recurrence_key = ?
                  AND e.episode_id != ? AND julianday(e.started_at) < julianday(?)
                  AND julianday(i.started_at) < julianday(?)
                ORDER BY julianday(e.started_at) DESC, e.episode_id DESC
                LIMIT 3
                """,
                (row["app_id"], row["recurrence_key"], row["episode_id"], row["started_at"], row["started_at"]),
            ).fetchall()
            candidates = [{**dict(item), "match_type": "same_target"} for item in exact]
            alert_identity = _alert_family(str(row["recurrence_key"]))
            # Live app IDs represent the registered workload; require its
            # captured cluster/namespace/name tuple instead of inferring from pod names.
            stable_workload = all(str(row[key] or "").strip() for key in ("workload_name", "namespace", "cluster"))
            if (
                len(candidates) < 3
                and alert_identity
                and stable_workload
                and row["source_kind"] == "live"
                and row["resource_kind"] == "pod"
                and str(row["resource_name"] or "")
            ):
                excluded_ids = [str(item["episode_id"]) for item in exact]
                exclusion_sql = ""
                exclusion_values: tuple[Any, ...] = ()
                if excluded_ids:
                    exclusion_sql = "AND e.episode_id NOT IN (" + ",".join("?" for _ in excluded_ids) + ")"
                    exclusion_values = tuple(excluded_ids)
                suffix = "|" + alert_identity
                related = connection.execute(
                    f"""
                    SELECT DISTINCT e.episode_id, e.title, e.started_at, e.ended_at, e.status, e.severity
                    FROM incident_episodes e
                    JOIN episode_incidents ei ON ei.episode_id = e.episode_id
                    JOIN incidents i ON i.incident_id = ei.incident_id
                    JOIN applications a ON a.app_id = e.app_id
                    WHERE e.app_id = ? AND i.app_id = e.app_id
                      AND a.name = ? AND a.namespace = ? AND a.cluster = ?
                      AND i.source_kind = 'live' AND i.resource_kind = 'pod'
                      AND i.resource_name != ?
                      AND substr(i.recurrence_key, -length(?)) = ?
                      AND e.episode_id != ? AND julianday(e.started_at) < julianday(?)
                      AND julianday(i.started_at) < julianday(?)
                      {exclusion_sql}
                    ORDER BY julianday(e.started_at) DESC, e.episode_id DESC
                    LIMIT ?
                    """,
                    (
                        row["app_id"], row["workload_name"], row["namespace"], row["cluster"],
                        row["resource_name"], suffix, suffix, row["episode_id"], row["started_at"],
                        row["started_at"],
                        *exclusion_values, 3 - len(candidates),
                    ),
                ).fetchall()
                candidates.extend({**dict(item), "match_type": "same_workload_different_pod"} for item in related)
        return [
            {**item, "reference": _reference("EP", str(item["episode_id"]))}
            for item in candidates[:3]
        ]

    def _recurrence_summary(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        key = str(row["recurrence_key"] or "")
        if not key:
            return {"previous_count": 0, "occurrence_count": 1, "candidates": []}
        matches = connection.execute(
            """
            SELECT episode_id, title, started_at, ended_at, status, severity, resource_kind, resource_name
            FROM incident_episodes
            WHERE recurrence_key = ?
            ORDER BY started_at ASC
            """,
            (key,),
        ).fetchall()
        current_start = self._parse_time(str(row["started_at"]))
        previous = [item for item in matches if item["episode_id"] != row["episode_id"] and self._parse_time(str(item["started_at"])) < current_start]
        points = sorted(self._parse_time(str(item["started_at"])) for item in matches)
        intervals = [
            (later - earlier).total_seconds()
            for earlier, later in zip(points, points[1:])
            if later > earlier
        ]
        candidates = [
            {
                "episode_id": str(item["episode_id"]),
                "reference": _reference("EP", str(item["episode_id"])),
                "title": str(item["title"]),
                "started_at": str(item["started_at"]),
                "ended_at": item["ended_at"],
                "status": str(item["status"]),
                "severity": str(item["severity"]),
            }
            for item in previous[-3:][::-1]
        ]
        return {
            "pattern_id": _reference("PAT", key),
            "previous_count": len(previous),
            "occurrence_count": len(matches),
            "first_seen_at": str(matches[0]["started_at"]) if matches else str(row["started_at"]),
            "last_seen_at": str(matches[-1]["started_at"]) if matches else str(row["started_at"]),
            "observed_interval_seconds": round(float(median(intervals)), 2) if intervals else None,
            "candidates": candidates,
        }

    def list_patterns(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recurring episode groups; a pattern is context, never a merged incident."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM incident_episodes
                WHERE recurrence_key != ''
                ORDER BY recurrence_key, started_at ASC
                """
            ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(str(row["recurrence_key"]), []).append(row)
        patterns = []
        for key, members in grouped.items():
            if len(members) < 2:
                continue
            intervals = [
                (self._parse_time(str(later["started_at"])) - self._parse_time(str(earlier["started_at"]))).total_seconds()
                for earlier, later in zip(members, members[1:])
                if self._parse_time(str(later["started_at"])) > self._parse_time(str(earlier["started_at"]))
            ]
            latest = members[-1]
            patterns.append(
                {
                    "pattern_id": _reference("PAT", key),
                    "title": str(latest["title"]),
                    "app_id": str(latest["app_id"]),
                    "resource": {"kind": str(latest["resource_kind"] or "application"), "name": str(latest["resource_name"] or "")},
                    "occurrence_count": len(members),
                    "first_seen_at": str(members[0]["started_at"]),
                    "last_seen_at": str(latest["started_at"]),
                    "observed_interval_seconds": round(float(median(intervals)), 2) if intervals else None,
                    "episodes": [
                        {"episode_id": str(item["episode_id"]), "reference": _reference("EP", str(item["episode_id"])),
                         "started_at": str(item["started_at"]), "status": str(item["status"])}
                        for item in members[-4:][::-1]
                    ],
                }
            )
        return sorted(patterns, key=lambda item: (item["last_seen_at"], item["occurrence_count"]), reverse=True)[:limit]

    def set_episode_archived(self, episode_id: str, archived: bool) -> dict[str, Any]:
        archived_at = utc_now() if archived else None
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE incident_episodes SET archived_at = ?, updated_at = ? WHERE episode_id = ?",
                (archived_at, utc_now(), episode_id),
            )
            if result.rowcount == 0:
                raise KeyError(f"Unknown episode: {episode_id}")
            connection.execute(
                """
                UPDATE incidents SET archived_at = ? WHERE incident_id IN
                    (SELECT incident_id FROM episode_incidents WHERE episode_id = ?)
                """,
                (archived_at, episode_id),
            )
        return self.get_episode(episode_id) or {}

    def episode_incident_ids(self, episode_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT incident_id FROM episode_incidents WHERE episode_id = ?", (episode_id,)
            ).fetchall()
        return [str(row["incident_id"]) for row in rows]

    def reconcile_live_incidents(self, active_incident_ids: set[str], observed_at: str) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT incident_id FROM incidents WHERE source_kind = 'live' AND lower(status) = 'firing'"
            ).fetchall()
            affected: set[str] = set()
            for row in rows:
                incident_id = str(row["incident_id"])
                if incident_id in active_incident_ids:
                    continue
                episode = connection.execute(
                    "SELECT episode_id FROM episode_incidents WHERE incident_id = ?", (incident_id,)
                ).fetchone()
                connection.execute(
                    "UPDATE incidents SET status = 'resolved', ended_at = ? WHERE incident_id = ?",
                    (observed_at, incident_id),
                )
                if episode:
                    affected.add(str(episode["episode_id"]))
            for episode_id in affected:
                self._refresh_episode(connection, episode_id)

    def activate_live_incident(self, incident_id: str) -> bool:
        """Promote a previously observed alert when Prometheus reports it firing."""

        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE incidents
                SET status = 'firing', ended_at = NULL, source_kind = 'live'
                WHERE incident_id = ?
                """,
                (incident_id,),
            )
            if result.rowcount == 0:
                return False
            self._assign_episode(connection, incident_id)
        return True

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
        return self._incident_row(row) if row else None

    def update_incident_identity(
        self,
        incident_id: str,
        resource_kind: str,
        resource_name: str,
        alert_identity: str,
    ) -> None:
        with self._connect() as connection:
            incident = connection.execute(
                "SELECT app_id FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            if not incident:
                return
            key = _recurrence_key(str(incident["app_id"]), resource_kind, resource_name, alert_identity)
            connection.execute(
                """
                UPDATE incidents
                SET resource_kind = ?, resource_name = ?, recurrence_key = ?
                WHERE incident_id = ?
                """,
                (resource_kind, resource_name, key, incident_id),
            )
            episode = connection.execute(
                "SELECT episode_id FROM episode_incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            if episode:
                self._refresh_episode(connection, str(episode["episode_id"]))

    def list_incidents(self, limit: int = 50, archived: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM incidents WHERE archived_at IS "
                + ("NOT NULL" if archived else "NULL")
                + " ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._incident_row(row) for row in rows]

    def set_incident_archived(self, incident_id: str, archived: bool) -> dict[str, Any]:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE incidents SET archived_at = ? WHERE incident_id = ?",
                (utc_now() if archived else None, incident_id),
            )
            if result.rowcount == 0:
                raise KeyError(f"Unknown incident: {incident_id}")
        return self.get_incident(incident_id) or {}

    def delete_incident(self, incident_id: str) -> None:
        with self._connect() as connection:
            episode = connection.execute(
                "SELECT episode_id FROM episode_incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            connection.execute("DELETE FROM capsules WHERE incident_id = ?", (incident_id,))
            result = connection.execute("DELETE FROM incidents WHERE incident_id = ?", (incident_id,))
            if result.rowcount == 0:
                raise KeyError(f"Unknown incident: {incident_id}")
            if episode:
                self._refresh_episode(connection, str(episode["episode_id"]))

    def incidents_older_than(self, cutoff: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM incidents WHERE created_at < ? ORDER BY created_at",
                (cutoff,),
            ).fetchall()
        return [self._incident_row(row) for row in rows]

    @staticmethod
    def _incident_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["trace_access"] = _decode(result.get("trace_access"), {})
        result["reference"] = _reference("INC", str(result["incident_id"]))
        result["resource"] = {
            "kind": str(result.get("resource_kind") or "application"),
            "name": str(result.get("resource_name") or ""),
        }
        return result

    def record_evidence_attachment(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind") or "")
        if kind not in {"image", "audio", "text"}:
            raise ValueError("Evidence attachment kind must be image, audio, or text")
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evidence_attachments
                    (attachment_id, episode_id, kind, filename, mime_type, storage_path, size_bytes, sha256,
                     observed_at, context_note, source_redacted, status, extraction, correction, provider,
                     model, usage, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload["attachment_id"]), str(payload["episode_id"]), kind,
                    str(payload["filename"]), str(payload["mime_type"]), str(payload["storage_path"]),
                    int(payload["size_bytes"]), str(payload["sha256"]), payload.get("observed_at"),
                    str(payload.get("context_note") or "")[:16000], int(bool(payload.get("source_redacted"))),
                    str(payload.get("status") or "queued"), _json(payload.get("extraction") or {}),
                    str(payload.get("correction") or "")[:2000], payload.get("provider"), payload.get("model"),
                    _json(payload.get("usage") or {}), now, now,
                ),
            )
        return self.get_evidence_attachment(str(payload["attachment_id"])) or {}

    @staticmethod
    def _attachment_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["source_redacted"] = bool(result.get("source_redacted"))
        result["extraction"] = _decode(result.get("extraction"), {})
        result["usage"] = _decode(result.get("usage"), {})
        return result

    def get_evidence_attachment(self, attachment_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM evidence_attachments WHERE attachment_id = ?", (attachment_id,)
            ).fetchone()
        return self._attachment_row(row) if row else None

    def list_evidence_attachments(self, episode_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evidence_attachments WHERE episode_id = ? ORDER BY created_at DESC", (episode_id,)
            ).fetchall()
        return [self._attachment_row(row) for row in rows]

    def update_evidence_attachment(
        self,
        attachment_id: str,
        *,
        status: str | None = None,
        extraction: dict[str, Any] | None = None,
        correction: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        usage: dict[str, Any] | None = None,
        observed_at: str | None = None,
        context_note: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_evidence_attachment(attachment_id)
        if not current:
            raise KeyError(f"Unknown evidence attachment: {attachment_id}")
        values = {
            "status": status if status is not None else current["status"],
            "extraction": _json(extraction if extraction is not None else current["extraction"]),
            "correction": str(correction if correction is not None else current["correction"])[:2000],
            "provider": provider if provider is not None else current["provider"],
            "model": model if model is not None else current["model"],
            "usage": _json(usage if usage is not None else current["usage"]),
            "observed_at": observed_at if observed_at is not None else current["observed_at"],
            "context_note": str(context_note if context_note is not None else current["context_note"])[:16000],
            "updated_at": utc_now(),
            "attachment_id": attachment_id,
        }
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE evidence_attachments
                SET status = :status, extraction = :extraction, correction = :correction, provider = :provider,
                    model = :model, usage = :usage, observed_at = :observed_at, context_note = :context_note,
                    updated_at = :updated_at
                WHERE attachment_id = :attachment_id
                """,
                values,
            )
        return self.get_evidence_attachment(attachment_id) or {}

    def delete_evidence_attachment(self, attachment_id: str) -> dict[str, Any]:
        current = self.get_evidence_attachment(attachment_id)
        if not current:
            raise KeyError(f"Unknown evidence attachment: {attachment_id}")
        with self._connect() as connection:
            connection.execute("DELETE FROM evidence_attachments WHERE attachment_id = ?", (attachment_id,))
        return current

    @staticmethod
    def _revision_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["evidence_manifest"] = _decode(result.get("evidence_manifest"), [])
        result["summary"] = _decode(result.get("summary"), {})
        return result

    def record_investigation_revision(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO investigation_revisions
                    (revision_id, episode_id, parent_revision_id, reason, source_mode, input_fingerprint,
                     status, evidence_manifest, state_path, summary, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(revision_id) DO UPDATE SET
                    status=excluded.status, evidence_manifest=excluded.evidence_manifest,
                    state_path=excluded.state_path, summary=excluded.summary, completed_at=excluded.completed_at
                """,
                (
                    str(payload["revision_id"]), str(payload["episode_id"]), payload.get("parent_revision_id"),
                    str(payload.get("reason") or "initial_capture"), str(payload.get("source_mode") or "live_sources"),
                    str(payload.get("input_fingerprint") or ""), str(payload.get("status") or "queued"),
                    _json(payload.get("evidence_manifest") or []), str(payload["state_path"]),
                    _json(payload.get("summary") or {}), payload.get("created_at") or now,
                    payload.get("completed_at"),
                ),
            )
        return self.get_investigation_revision(str(payload["revision_id"])) or {}

    def get_investigation_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investigation_revisions WHERE revision_id = ?", (revision_id,)
            ).fetchone()
        return self._revision_row(row) if row else None

    def list_investigation_revisions(self, episode_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM investigation_revisions WHERE episode_id = ? ORDER BY created_at DESC", (episode_id,)
            ).fetchall()
        return [self._revision_row(row) for row in rows]

    @staticmethod
    def _source_review_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["result"] = _decode(result.get("result"), {})
        result["usage"] = _decode(result.get("usage"), {})
        return result

    def record_source_disconnected_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_disconnected_reviews
                    (review_id, episode_id, question, input_fingerprint, model, status, state_path,
                     result, usage, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                    status=excluded.status, result=excluded.result, usage=excluded.usage,
                    state_path=excluded.state_path, completed_at=excluded.completed_at
                """,
                (
                    str(payload["review_id"]), str(payload["episode_id"]), str(payload["question"])[:500],
                    str(payload.get("input_fingerprint") or ""), str(payload["model"]),
                    str(payload.get("status") or "queued"), str(payload["state_path"]),
                    _json(payload.get("result") or {}), _json(payload.get("usage") or {}),
                    payload.get("created_at") or now, payload.get("completed_at"),
                ),
            )
        return self.get_source_disconnected_review(str(payload["review_id"])) or {}

    def get_source_disconnected_review(self, review_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_disconnected_reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
        return self._source_review_row(row) if row else None

    def list_source_disconnected_reviews(self, episode_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_disconnected_reviews WHERE episode_id = ? ORDER BY created_at DESC", (episode_id,)
            ).fetchall()
        return [self._source_review_row(row) for row in rows]

    def upsert_related_episode_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        episode_ids = sorted({str(item) for item in payload.get("episode_ids", []) if item})
        if len(episode_ids) < 2:
            raise ValueError("A related episode group needs at least two episodes")
        correlation_key = str(payload["correlation_key"])
        group_id = str(payload["group_id"])
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO related_episode_groups
                    (group_id, correlation_key, cluster, title, status, severity, relationship, basis, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(correlation_key) DO UPDATE SET
                    title=excluded.title, status=excluded.status, severity=excluded.severity,
                    relationship=excluded.relationship, basis=excluded.basis, updated_at=excluded.updated_at
                """,
                (
                    group_id, correlation_key, str(payload["cluster"]), str(payload["title"]),
                    str(payload["status"]), str(payload["severity"]), str(payload["relationship"]),
                    _json(payload.get("basis") or []), now, now,
                ),
            )
            row = connection.execute(
                "SELECT group_id FROM related_episode_groups WHERE correlation_key = ?", (correlation_key,)
            ).fetchone()
            actual_group_id = str(row["group_id"])
            excluded = {
                str(item["episode_id"])
                for item in connection.execute(
                    "SELECT episode_id FROM related_group_exclusions WHERE correlation_key = ?", (correlation_key,)
                ).fetchall()
            }
            for episode_id in episode_ids:
                if episode_id not in excluded:
                    connection.execute(
                        "INSERT OR IGNORE INTO related_group_members (group_id, episode_id) VALUES (?, ?)",
                        (actual_group_id, episode_id),
                    )
        return self.get_related_episode_group(actual_group_id) or {}

    def _related_group_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row, include_archived: bool = True,
    ) -> dict[str, Any]:
        result = dict(row)
        result["reference"] = _reference("GRP", str(row["group_id"]))
        result["basis"] = _decode(result.get("basis"), [])
        members = connection.execute(
            """
            SELECT e.* FROM incident_episodes e
            JOIN related_group_members gm ON gm.episode_id = e.episode_id
            WHERE gm.group_id = ?
            """ + ("" if include_archived else " AND e.archived_at IS NULL") + """
            ORDER BY e.started_at
            """,
            (row["group_id"],),
        ).fetchall()
        result["episodes"] = [
            {
                "episode_id": str(item["episode_id"]), "reference": _reference("EP", str(item["episode_id"])),
                "title": str(item["title"]), "status": str(item["status"]), "severity": str(item["severity"]),
                "started_at": str(item["started_at"]), "last_activity_at": str(item["last_activity_at"]),
                "app_id": str(item["app_id"]),
            }
            for item in members
        ]
        result["episode_count"] = len(result["episodes"])
        result["active_count"] = sum(1 for item in result["episodes"] if item["status"] == "active")
        result["first_observed_at"] = result["episodes"][0]["started_at"] if result["episodes"] else None
        result["last_observed_at"] = max(
            (item["last_activity_at"] for item in result["episodes"]), default=None,
        )
        return result

    def get_related_episode_group(self, group_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM related_episode_groups WHERE group_id = ?", (group_id,)
            ).fetchone()
            return self._related_group_row(connection, row) if row else None

    def list_related_episode_groups(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT g.* FROM related_episode_groups g
                WHERE (
                    SELECT COUNT(*)
                    FROM related_group_members gm
                    JOIN incident_episodes e ON e.episode_id = gm.episode_id
                    WHERE gm.group_id = g.group_id AND e.archived_at IS NULL
                ) >= 2
                ORDER BY g.updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._related_group_row(connection, row, include_archived=False) for row in rows]

    def separate_related_episode(self, group_id: str, episode_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            group = connection.execute(
                "SELECT correlation_key FROM related_episode_groups WHERE group_id = ?", (group_id,)
            ).fetchone()
            if not group:
                raise KeyError("Related episode group not found")
            member = connection.execute(
                "SELECT 1 FROM related_group_members WHERE group_id = ? AND episode_id = ?", (group_id, episode_id)
            ).fetchone()
            if not member:
                raise KeyError("Episode is not part of this related group")
            connection.execute(
                "DELETE FROM related_group_members WHERE group_id = ? AND episode_id = ?", (group_id, episode_id)
            )
            connection.execute(
                "INSERT OR IGNORE INTO related_group_exclusions (correlation_key, episode_id, created_at) VALUES (?, ?, ?)",
                (str(group["correlation_key"]), episode_id, utc_now()),
            )
            connection.execute(
                "UPDATE related_episode_groups SET updated_at = ? WHERE group_id = ?", (utc_now(), group_id)
            )
        return self.get_related_episode_group(group_id) or {}

    def record_capsule(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO capsules
                    (capsule_id, incident_id, app_id, status, output_dir, archive_path,
                     size_bytes, selected_evidence, compression, signal_preservation,
                     grounding, runtime_seconds, model_winner, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(capsule_id) DO UPDATE SET
                    status=excluded.status,
                    archive_path=excluded.archive_path,
                    size_bytes=excluded.size_bytes,
                    selected_evidence=excluded.selected_evidence,
                    compression=excluded.compression,
                    signal_preservation=excluded.signal_preservation,
                    grounding=excluded.grounding,
                    runtime_seconds=excluded.runtime_seconds,
                    model_winner=excluded.model_winner
                """,
                (
                    payload["capsule_id"],
                    payload["incident_id"],
                    payload["app_id"],
                    payload.get("status", "ready"),
                    str(payload["output_dir"]),
                    str(payload.get("archive_path", "")),
                    int(payload.get("size_bytes", 0)),
                    int(payload.get("selected_evidence", 0)),
                    float(payload.get("compression", 0)),
                    float(payload.get("signal_preservation", 0)),
                    float(payload.get("grounding", 0)),
                    float(payload.get("runtime_seconds", 0)),
                    payload.get("model_winner"),
                    payload.get("created_at", utc_now()),
                ),
            )
        return self.get_capsule(str(payload["capsule_id"])) or {}

    def get_capsule(self, capsule_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM capsules WHERE capsule_id = ?", (capsule_id,)).fetchone()
        return dict(row) if row else None

    def get_capsule_for_incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM capsules WHERE incident_id = ? ORDER BY created_at DESC LIMIT 1",
                (incident_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_capsules(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM capsules ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def update_capsule_model_winner(self, capsule_id: str, winner: str | None) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE capsules SET model_winner = ? WHERE capsule_id = ?",
                (winner, capsule_id),
            )
            if result.rowcount == 0:
                raise KeyError(f"Unknown capsule: {capsule_id}")

    def list_model_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM model_profiles ORDER BY model_id").fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]

    def update_model_profile(self, model_id: str, enabled: bool, max_tokens: int) -> dict[str, Any]:
        if max_tokens < 256 or max_tokens > 16000:
            raise ValueError("max_tokens must be between 256 and 16000")
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE model_profiles
                SET enabled = ?, max_tokens = ?, updated_at = ?
                WHERE model_id = ?
                """,
                (int(enabled), max_tokens, utc_now(), model_id),
            )
            if result.rowcount == 0:
                raise KeyError(f"Unknown model profile: {model_id}")
        return next(item for item in self.list_model_profiles() if item["model_id"] == model_id)

    def upsert_model_profile(self, model_id: str, provider: str, enabled: bool, max_tokens: int) -> dict[str, Any]:
        if not model_id or any(character.isspace() for character in model_id):
            raise ValueError("model_id must be a non-empty identifier without spaces")
        if provider not in {"deepseek", "openrouter"}:
            raise ValueError("provider must be deepseek or openrouter")
        if max_tokens < 256 or max_tokens > 16000:
            raise ValueError("max_tokens must be between 256 and 16000")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO model_profiles (model_id, provider, max_tokens, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(model_id) DO UPDATE SET
                    provider=excluded.provider,
                    max_tokens=excluded.max_tokens,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (model_id, provider, max_tokens, int(enabled), utc_now()),
            )
        return next(item for item in self.list_model_profiles() if item["model_id"] == model_id)

    def get_setting(self, setting_key: str, fallback: str | None = None) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT setting_value FROM settings WHERE setting_key = ?", (setting_key,)
            ).fetchone()
        return str(row["setting_value"]) if row else fallback

    def set_setting(self, setting_key: str, setting_value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value=excluded.setting_value,
                    updated_at=excluded.updated_at
                """,
                (setting_key, setting_value, utc_now()),
            )

    def enqueue_atlas_publication(
        self, payload: dict[str, Any], pending_limit: int = 1000, *,
        local_episode_id: str | None = None, investigation_revision_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a compact, already-projected case before any network attempt."""
        envelope = dict(payload)
        envelope.pop("revision", None)
        serialized = _json(envelope)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        instance_id = str(envelope.get("instance_id") or "")
        episode_id = str(envelope.get("episode_id") or "")
        fingerprint = str(envelope.get("fingerprint") or "")
        if not instance_id or not episode_id or not fingerprint:
            raise ValueError("Atlas outbox payload needs instance_id, episode_id, and fingerprint")
        if len(serialized.encode("utf-8")) > 24 * 1024:
            raise ValueError("Atlas outbox payload exceeds 24 KiB")
        local_revision_id = str(investigation_revision_id or "").strip()
        if len(local_revision_id) > 200:
            local_revision_id = local_revision_id[:200]
        local_id = str(local_episode_id or "").strip()
        if len(local_id) > 200:
            local_id = local_id[:200]
        now = utc_now()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM atlas_outbox WHERE status != 'pending' AND created_at < ?", (cutoff,)
            )
            existing = connection.execute(
                "SELECT * FROM atlas_outbox WHERE instance_id = ? AND episode_id = ? AND payload_digest = ?",
                (instance_id, episode_id, digest),
            ).fetchone()
            if existing:
                revision_ids = _decode(existing["investigation_revision_ids"], [])
                if not isinstance(revision_ids, list):
                    revision_ids = []
                revision_ids = [str(item) for item in revision_ids if isinstance(item, str)]
                links_truncated = bool(existing["investigation_revision_links_truncated"])
                if len(revision_ids) > MAX_PUBLICATION_REVISION_LINKS:
                    revision_ids = revision_ids[-MAX_PUBLICATION_REVISION_LINKS:]
                    links_truncated = True
                changed = False
                if local_revision_id and local_revision_id not in revision_ids:
                    revision_ids.append(local_revision_id)
                    changed = True
                if len(revision_ids) > MAX_PUBLICATION_REVISION_LINKS:
                    revision_ids = revision_ids[-MAX_PUBLICATION_REVISION_LINKS:]
                    links_truncated = True
                if changed or links_truncated != bool(existing["investigation_revision_links_truncated"]):
                    connection.execute(
                        "UPDATE atlas_outbox SET investigation_revision_ids = ?, "
                        "investigation_revision_links_truncated = ?, "
                        "local_episode_id = CASE WHEN local_episode_id = '' THEN ? ELSE local_episode_id END, "
                        "local_episode_link_status = CASE WHEN local_episode_id = '' AND ? != '' "
                        "THEN 'linked' ELSE local_episode_link_status END "
                        "WHERE outbox_id = ?",
                        (_json(revision_ids), int(links_truncated), local_id, local_id,
                         int(existing["outbox_id"])),
                    )
                    existing = connection.execute(
                        "SELECT * FROM atlas_outbox WHERE outbox_id = ?", (int(existing["outbox_id"]),)
                    ).fetchone()
                elif local_id and not str(existing["local_episode_id"] or ""):
                    connection.execute(
                        "UPDATE atlas_outbox SET local_episode_id = ?, local_episode_link_status = 'linked' "
                        "WHERE outbox_id = ?",
                        (local_id, int(existing["outbox_id"])),
                    )
                    existing = connection.execute(
                        "SELECT * FROM atlas_outbox WHERE outbox_id = ?", (int(existing["outbox_id"]),)
                    ).fetchone()
                return {**dict(existing), "payload": _decode(existing["payload"], {}), "is_new": False}
            head = connection.execute(
                "SELECT revision, payload_digest, status FROM atlas_publication_heads "
                "WHERE instance_id = ? AND episode_id = ?", (instance_id, episode_id),
            ).fetchone()
            if head and head["payload_digest"] == digest and head["status"] == "sent":
                return {"accepted": True, "is_new": False, "status": "sent", "revision": head["revision"]}
            pending = int(connection.execute(
                "SELECT COUNT(*) FROM atlas_outbox WHERE status = 'pending'"
            ).fetchone()[0])
            if pending >= max(1, pending_limit):
                self.set_setting("atlas_outbox_quota_error", "Pending Atlas outbox quota reached")
                return {"accepted": False, "reason": "quota_exceeded", "pending_count": pending}
            if head:
                revision = int(head["revision"]) + 1
            else:
                # Existing installations may have pruned the old outbox. SQLite's
                # AUTOINCREMENT high-water mark still exceeds every old case revision.
                sequence = connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = 'atlas_outbox'"
                ).fetchone()
                revision = int(sequence["seq"] if sequence else 0) + 1
            envelope["revision"] = revision
            connection.execute(
                """
                INSERT INTO atlas_outbox
                    (instance_id, episode_id, local_episode_id, local_episode_link_status,
                     revision, fingerprint, payload_digest, payload,
                     status, attempts, next_attempt_at, investigation_revision_ids,
                     receipt_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, 'not_yet_confirmed', ?, ?)
                """,
                (instance_id, episode_id, local_id, "linked" if local_id else "unknown",
                 revision, fingerprint, digest, _json(envelope), now,
                 _json([local_revision_id] if local_revision_id else []), now, now),
            )
            connection.execute(
                "INSERT INTO atlas_publication_heads (instance_id, episode_id, revision, payload_digest, status) "
                "VALUES (?, ?, ?, ?, 'pending') ON CONFLICT(instance_id, episode_id) DO UPDATE SET "
                "revision=excluded.revision, payload_digest=excluded.payload_digest, status=excluded.status",
                (instance_id, episode_id, revision, digest),
            )
            connection.execute(
                """
                DELETE FROM atlas_outbox WHERE status != 'pending' AND outbox_id NOT IN
                    (SELECT outbox_id FROM atlas_outbox WHERE status != 'pending'
                     ORDER BY updated_at DESC, outbox_id DESC LIMIT 5000)
                """
            )
            result = connection.execute(
                "SELECT * FROM atlas_outbox WHERE instance_id = ? AND episode_id = ? AND payload_digest = ?",
                (instance_id, episode_id, digest),
            ).fetchone()
            connection.execute("DELETE FROM settings WHERE setting_key = 'atlas_outbox_quota_error'")
            return {**dict(result), "payload": _decode(result["payload"], {}), "is_new": True}

    def due_atlas_publications(self, limit: int = 10) -> list[dict[str, Any]]:
        now = utc_now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM atlas_outbox
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY created_at, outbox_id LIMIT ?
                """,
                (now, max(1, min(50, int(limit)))),
            ).fetchall()
        return [{**dict(row), "payload": _decode(row["payload"], {})} for row in rows]

    def complete_atlas_publication(self, outbox_id: int, remote_case_id: str | None = None) -> None:
        candidate_id = str(remote_case_id or "").strip()
        safe_case_id = candidate_id if re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", candidate_id) else None
        receipt_status = "remote_id_recorded" if safe_case_id else "remote_id_not_returned"
        with self._connect() as connection:
            row = connection.execute(
                "SELECT instance_id, episode_id, revision FROM atlas_outbox WHERE outbox_id = ?", (int(outbox_id),)
            ).fetchone()
            connection.execute(
                "UPDATE atlas_outbox SET status = 'sent', last_error = NULL, remote_case_id = ?, "
                "receipt_status = ?, updated_at = ? WHERE outbox_id = ?",
                (safe_case_id, receipt_status, utc_now(), int(outbox_id)),
            )
            if row:
                connection.execute(
                    "UPDATE atlas_publication_heads SET status = 'sent' "
                    "WHERE instance_id = ? AND episode_id = ? AND revision = ?",
                    (row["instance_id"], row["episode_id"], row["revision"]),
                )

    def defer_atlas_publication(
        self, outbox_id: int, error: str, delay_seconds: int, *, permanent: bool = False,
    ) -> None:
        now = utc_now()
        next_at = (datetime.now(timezone.utc) + timedelta(seconds=max(1, min(3600, delay_seconds)))).isoformat().replace("+00:00", "Z")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE atlas_outbox SET status = ?, attempts = attempts + 1, next_attempt_at = ?,
                    last_error = ?, updated_at = ? WHERE outbox_id = ?
                """,
                ("failed" if permanent else "pending", next_at, str(error)[:240], now, int(outbox_id)),
            )

    def retry_failed_atlas_publications(self, limit: int = 100, auth_only: bool = False) -> int:
        limit = max(1, min(500, int(limit)))
        now = utc_now()
        filter_sql = (
            "AND (last_error LIKE 'Estima returned HTTP 401%' OR last_error LIKE 'Estima returned HTTP 403%' "
            "OR last_error LIKE 'Atlas returned HTTP 401%' OR last_error LIKE 'Atlas returned HTTP 403%')"
        ) if auth_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT outbox_id FROM atlas_outbox WHERE status = 'failed' {filter_sql} "
                "ORDER BY updated_at, outbox_id LIMIT ?",
                (limit,),
            ).fetchall()
            ids = [int(row["outbox_id"]) for row in rows]
            if not ids:
                return 0
            connection.executemany(
                "UPDATE atlas_outbox SET status = 'pending', attempts = 0, next_attempt_at = ?, "
                "last_error = NULL, updated_at = ? WHERE outbox_id = ?",
                [(now, now, outbox_id) for outbox_id in ids],
            )
        return len(ids)

    def atlas_outbox_status(self) -> dict[str, Any]:
        with self._connect() as connection:
            counts = connection.execute(
                "SELECT status, COUNT(*) AS count FROM atlas_outbox GROUP BY status"
            ).fetchall()
            row = connection.execute(
                "SELECT last_error FROM atlas_outbox WHERE last_error IS NOT NULL ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            pending = int(connection.execute(
                "SELECT COUNT(*) FROM atlas_outbox WHERE status = 'pending'"
            ).fetchone()[0])
            failed = int(connection.execute(
                "SELECT COUNT(*) FROM atlas_outbox WHERE status = 'failed'"
            ).fetchone()[0])
        quota_error = self.get_setting("atlas_outbox_quota_error")
        return {"pending_count": pending, "failed_count": failed,
                "last_error": (str(row["last_error"]) if row else None) or quota_error,
                "counts": {str(item["status"]): int(item["count"]) for item in counts}}

    @staticmethod
    def _publication_error_summary(error: str | None) -> str | None:
        """Return a fixed, safe summary rather than stored exception text."""
        if not error:
            return None
        value = str(error).strip()
        match = re.fullmatch(r"(?:Estima|Atlas) returned HTTP (\d{3})", value)
        if match:
            code = int(match.group(1))
            if code in {401, 403}:
                return f"Collective rejected the service credential (HTTP {code})."
            if 400 <= code < 500:
                return f"Collective rejected the case (HTTP {code})."
            return f"Collective service returned HTTP {code}."
        lowered = value.lower()
        if "unavailable" in lowered or "timeout" in lowered:
            return "Collective is unavailable; delivery will retry."
        if "invalid json" in lowered or "non-object" in lowered:
            return "Collective returned an invalid response."
        if "exceeded" in lowered and "response" in lowered:
            return "Collective returned an oversized response."
        return "Publication delivery failed; details are hidden."

    def publication_provenance(self, episode_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return bounded local delivery receipts without case payloads or secrets."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM atlas_outbox WHERE local_episode_id = ? "
                "ORDER BY revision DESC, outbox_id DESC LIMIT ?",
                (str(episode_id), max(1, min(50, int(limit)))),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            internal_status = str(item.get("status") or "pending")
            attempts = max(0, int(item.get("attempts") or 0))
            if internal_status == "sent":
                status = "published"
            elif internal_status == "failed":
                status = "attention_required"
            elif attempts:
                status = "retry_scheduled"
            else:
                status = "queued"
            receipt_status = str(item.get("receipt_status") or "legacy_receipt_unknown")
            if receipt_status not in {
                "not_yet_confirmed", "remote_id_recorded", "remote_id_not_returned", "legacy_receipt_unknown",
            }:
                receipt_status = "legacy_receipt_unknown"
            revision_ids = _decode(item.get("investigation_revision_ids"), [])
            if not isinstance(revision_ids, list):
                revision_ids = []
            results.append({
                "episode_id": str(item["local_episode_id"]),
                "local_episode_link_status": str(item["local_episode_link_status"]),
                "investigation_revision_ids": [str(value) for value in revision_ids if isinstance(value, str)],
                "investigation_revision_links_truncated": bool(item["investigation_revision_links_truncated"]),
                "status": status,
                "receipt_status": receipt_status,
                "revision": int(item["revision"]),
                "created_at": str(item["created_at"]),
                "updated_at": str(item["updated_at"]),
                "attempts": attempts,
                "next_attempt_at": str(item["next_attempt_at"]) if internal_status == "pending" else None,
                "last_error": self._publication_error_summary(item.get("last_error")),
                "remote_case_id": item.get("remote_case_id") if receipt_status == "remote_id_recorded" else None,
            })
        return results

    def overview(self) -> dict[str, Any]:
        applications = self.list_applications()
        incidents = self.list_incidents()
        archived_incidents = self.list_incidents(archived=True)
        episodes = self.list_episodes()
        archived_episodes = self.list_episodes(archived=True)
        patterns = self.list_patterns()
        capsules = self.list_capsules()
        observed_applications = [item for item in applications if item["status"] != "not_observed"]
        return {
            "applications": applications,
            "incidents": incidents,
            "archived_incidents": archived_incidents,
            "episodes": episodes,
            "archived_episodes": archived_episodes,
            "patterns": patterns,
            "capsules": capsules,
            "models": self.list_model_profiles(),
            "totals": {
                "applications": len(observed_applications),
                "degraded_applications": sum(1 for item in observed_applications if item["status"] == "degraded"),
                "incidents": len(episodes),
                "archived_incidents": len(archived_episodes),
                "signals": len(incidents),
                "capsules": len(capsules),
                "raw_bytes_observed": sum(int(item["raw_bytes"]) for item in incidents),
                "capsule_bytes_retained": sum(int(item["size_bytes"]) for item in capsules),
            },
        }
