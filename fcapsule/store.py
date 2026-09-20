"""Persistent control-plane metadata for FCAPSule.

Raw telemetry remains in the configured observability systems. This store keeps
application registrations, incident/capsule metadata, and model preferences.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_MODEL_PROFILES = (
    ("deepseek-v4-flash", "deepseek", 2400, False),
    ("deepseek-v4-pro", "deepseek", 3600, True),
)

EPISODE_JOIN_MINUTES = 15
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


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


class FCAPSuleStore:
    """Small SQLite repository used by the CLI and local control plane."""

    def __init__(self, path: str | Path = ".fcapsule/fcapsule.db") -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
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
                    source_kind TEXT NOT NULL DEFAULT 'external'
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
                    archived_at TEXT
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

                CREATE INDEX IF NOT EXISTS idx_incidents_app_time
                    ON incidents(app_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_capsules_app_time
                    ON capsules(app_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_episodes_app_activity
                    ON incident_episodes(app_id, last_activity_at DESC);
                CREATE INDEX IF NOT EXISTS idx_episode_incidents_episode
                    ON episode_incidents(episode_id);
                """
            )
            incident_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(incidents)").fetchall()
            }
            if "archived_at" not in incident_columns:
                connection.execute("ALTER TABLE incidents ADD COLUMN archived_at TEXT")
            if "source_kind" not in incident_columns:
                connection.execute("ALTER TABLE incidents ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'external'")
            connection.execute(
                "UPDATE incidents SET source_kind = 'live' WHERE case_dir LIKE ?",
                ("%/live-cases/%",),
            )
            self._backfill_episodes(connection)
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
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents
                    (incident_id, app_id, scenario, status, severity, started_at, ended_at,
                     case_dir, alert_count, log_count, metric_series_count, raw_bytes,
                     trace_access, summary, created_at, source_kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    status=excluded.status,
                    ended_at=excluded.ended_at,
                    alert_count=excluded.alert_count,
                    log_count=excluded.log_count,
                    metric_series_count=excluded.metric_series_count,
                    raw_bytes=excluded.raw_bytes,
                    trace_access=excluded.trace_access,
                    summary=excluded.summary,
                    source_kind=excluded.source_kind
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
                ),
            )
            if str(payload.get("status", "firing")).lower() != "pending":
                self._assign_episode(connection, incident_id)
            connection.execute(
                "UPDATE applications SET status = ?, updated_at = ? WHERE app_id = ?",
                ("degraded", now, payload["app_id"]),
            )
        return self.get_incident(incident_id) or {}

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

    def _assign_episode(self, connection: sqlite3.Connection, incident_id: str) -> str:
        linked = connection.execute(
            "SELECT episode_id FROM episode_incidents WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if linked:
            episode_id = str(linked["episode_id"])
            self._refresh_episode(connection, episode_id)
            return episode_id

        incident = connection.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
        if not incident:
            raise KeyError(f"Unknown incident: {incident_id}")
        started_at = str(incident["started_at"])
        started = self._parse_time(started_at)
        lower_bound = (started - timedelta(minutes=EPISODE_JOIN_MINUTES)).isoformat().replace("+00:00", "Z")
        upper_bound = (started + timedelta(minutes=EPISODE_JOIN_MINUTES)).isoformat().replace("+00:00", "Z")
        candidate = connection.execute(
            """
            SELECT episode_id
            FROM incident_episodes
            WHERE app_id = ? AND archived_at IS NULL
              AND last_activity_at >= ? AND started_at <= ?
            ORDER BY last_activity_at DESC
            LIMIT 1
            """,
            (incident["app_id"], lower_bound, upper_bound),
        ).fetchone()
        if candidate:
            episode_id = str(candidate["episode_id"])
        else:
            episode_id = f"episode-{incident_id}"
            now = utc_now()
            connection.execute(
                """
                INSERT INTO incident_episodes
                    (episode_id, app_id, title, status, severity, started_at, last_activity_at,
                     ended_at, primary_incident_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    incident["app_id"],
                    incident["summary"] or incident["scenario"],
                    "active" if str(incident["status"]).lower() == "firing" else "resolved",
                    incident["severity"],
                    started_at,
                    started_at,
                    None if str(incident["status"]).lower() == "firing" else incident["ended_at"],
                    incident_id,
                    now,
                    now,
                ),
            )
        connection.execute(
            "INSERT OR IGNORE INTO episode_incidents (episode_id, incident_id) VALUES (?, ?)",
            (episode_id, incident_id),
        )
        self._refresh_episode(connection, episode_id)
        return episode_id

    def _refresh_episode(self, connection: sqlite3.Connection, episode_id: str) -> None:
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
        primary = max(
            signals,
            key=lambda item: (SEVERITY_RANK.get(str(item["severity"]).lower(), 0), str(item["started_at"])),
        )
        active = any(str(item["status"]).lower() == "firing" for item in signals)
        ended_values = [str(item["ended_at"]) for item in signals if item["ended_at"]]
        connection.execute(
            """
            UPDATE incident_episodes
            SET title = ?, status = ?, severity = ?, started_at = ?, last_activity_at = ?,
                ended_at = ?, primary_incident_id = ?, updated_at = ?
            WHERE episode_id = ?
            """,
            (
                primary["summary"] or primary["scenario"],
                "active" if active else "resolved",
                primary["severity"],
                min(str(item["started_at"]) for item in signals),
                max(str(item["started_at"]) for item in signals),
                None if active else (max(ended_values) if ended_values else max(str(item["started_at"]) for item in signals)),
                primary["incident_id"],
                utc_now(),
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
        return result

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
        return result

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
        if provider != "deepseek":
            raise ValueError("Only the configured DeepSeek-compatible provider is supported by this runtime")
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

    def overview(self) -> dict[str, Any]:
        applications = self.list_applications()
        incidents = self.list_incidents()
        archived_incidents = self.list_incidents(archived=True)
        episodes = self.list_episodes()
        archived_episodes = self.list_episodes(archived=True)
        capsules = self.list_capsules()
        observed_applications = [item for item in applications if item["status"] != "not_observed"]
        return {
            "applications": applications,
            "incidents": incidents,
            "archived_incidents": archived_incidents,
            "episodes": episodes,
            "archived_episodes": archived_episodes,
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
