"""Background projection and durable retry worker for Estima publication."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from fcapsule.estima_client import EstimaClientError
from fcapsule.estima_projection import project_estima_record


TERMINAL_STATES = {"ready", "inconclusive", "incomplete", "not_configured"}


def _remote_case_id(response: Any, service_token: str = "") -> str | None:
    if not isinstance(response, dict):
        return None
    candidates = [response]
    candidates.extend(value for key in ("case", "record", "data")
                      if isinstance((value := response.get(key)), dict))
    for candidate in candidates:
        for key in ("case_id", "id"):
            value = candidate.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                value = str(value).strip()
                if (re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", value)
                        and not (service_token and value == service_token)):
                    return value
    return None


class EstimaPublisher:
    """Scans retained completed cases, persists projections, and retries off-thread."""

    def __init__(self, plane, interval_seconds: float = 5.0) -> None:
        self.plane = plane
        self.interval_seconds = max(5.0, interval_seconds)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._thread_guard = threading.Lock()
        self._queue_lock = threading.Lock()
        self._notified: set[str] = set()
        self._full_scan_pending = True
        self._last_full_scan = 0.0
        self._seen: dict[str, tuple[Any, ...]] = {}

    def start(self) -> None:
        with self._thread_guard:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            with self._queue_lock:
                self._full_scan_pending = True
            self.thread = threading.Thread(target=self._run, daemon=True, name="fcapsule-estima-publisher")
            self.thread.start()

    def wake(self) -> None:
        self.wake_event.set()

    def request_full_scan(self) -> None:
        with self._queue_lock:
            self._full_scan_pending = True
        self.wake_event.set()

    def notify_episode(self, episode_id: str) -> None:
        """Queue a terminal episode ID without performing projection or network I/O."""
        try:
            if not self.plane._effective_estima_settings().get("publish_enabled"):
                return
            with self._queue_lock:
                if len(self._notified) >= 500:
                    self._notified.clear()
                    self._full_scan_pending = True
                else:
                    self._notified.add(str(episode_id))
            self.start()
            self.wake_event.set()
        except Exception:
            self.plane.store.set_setting("atlas_projection_last_error", "Estima completion hook unavailable")

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.wake_event.wait(self.interval_seconds)
            self.wake_event.clear()
            if self.stop_event.is_set():
                break
            try:
                with self._queue_lock:
                    full_scan, self._full_scan_pending = self._full_scan_pending, False
                    episode_ids = list(self._notified)
                    self._notified.clear()
                if not self.plane._effective_estima_settings().get("publish_enabled"):
                    continue
                periodic_scan = time.monotonic() - self._last_full_scan >= 600
                with self._lock:
                    if full_scan or periodic_scan or episode_ids:
                        self.scan_completed(None if full_scan or periodic_scan else episode_ids)
                    if full_scan or periodic_scan:
                        self._last_full_scan = time.monotonic()
                    self._drain(limit=4)
            except Exception as error:
                self.plane.store.set_setting("atlas_projection_last_error", f"Estima worker unavailable ({type(error).__name__})")

    def shutdown(self, drain: bool = True, timeout: float = 7.0) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=max(0.1, timeout))
        if drain:
            try:
                with self._lock:
                    self._drain(limit=2)
            except Exception:
                pass

    def _retained_episode(self, episode: dict[str, Any]) -> list[dict[str, Any]]:
        retained = []
        for signal in episode.get("signals", [])[-3:]:
            record = self.plane.store.get_capsule_for_incident(str(signal.get("incident_id") or ""))
            if not record:
                continue
            root = Path(record["output_dir"])
            capsule_path = root / "capsule.json"
            if not capsule_path.is_file():
                continue
            try:
                if capsule_path.stat().st_size > 8 * 1024 * 1024:
                    continue
                capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
                if not isinstance(capsule, dict):
                    continue
            except (OSError, ValueError):
                continue
            report = {}
            report_path = root / "incident_report.json"
            try:
                if report_path.is_file() and report_path.stat().st_size <= 2 * 1024 * 1024:
                    value = json.loads(report_path.read_text(encoding="utf-8"))
                    report = value if isinstance(value, dict) else {}
            except (OSError, ValueError):
                pass
            retained.append({"capsule": capsule, "report": report})
        return retained

    def _signature(self, episode: dict[str, Any]) -> tuple[Any, ...]:
        signature: list[Any] = []
        state_path = self.plane.investigator.path(str(episode["episode_id"]))
        try:
            stat = state_path.stat()
            signature.extend((stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.extend((0, 0))
        for signal in episode.get("signals", [])[-3:]:
            record = self.plane.store.get_capsule_for_incident(str(signal.get("incident_id") or ""))
            if not record:
                signature.extend((0, 0, 0, 0))
                continue
            root = Path(record["output_dir"])
            for name in ("capsule.json", "incident_report.json"):
                try:
                    stat = (root / name).stat()
                    signature.extend((stat.st_mtime_ns, stat.st_size))
                except OSError:
                    signature.extend((0, 0))
        return tuple(signature)

    def scan_completed(self, episode_ids: list[str] | None = None) -> int:
        config = self.plane._effective_estima_settings()
        if not config.get("publish_enabled") or not config.get("url"):
            return 0
        queued = 0
        projection_error = None
        if episode_ids is None:
            episodes = [
                *self.plane.store.list_episodes(limit=10000, archived=False),
                *self.plane.store.list_episodes(limit=10000, archived=True),
            ]
        else:
            episodes = [episode for episode_id in set(episode_ids)
                        if (episode := self.plane.store.get_episode(str(episode_id))) is not None]
        for episode in episodes:
            episode_id = str(episode["episode_id"])
            try:
                if any(signal.get("source_kind") == "imported" for signal in episode.get("signals", [])):
                    self._seen[episode_id] = self._signature(episode)
                    continue
                if self.plane.store.collective_withdrawal_status(episode_id):
                    self._seen[episode_id] = self._signature(episode)
                    continue
                signature = self._signature(episode)
                if self._seen.get(episode_id) == signature:
                    continue
                state = self.plane.investigator.read(episode_id)
                if state.get("status") not in TERMINAL_STATES:
                    self._seen[episode_id] = signature
                    continue
                retained = self._retained_episode(episode)
                if not retained:
                    continue
                app = self.plane.store.get_application(str(episode["app_id"]))
                payload = project_estima_record(
                    str(config.get("instance_id") or ""), episode, state, retained, app,
                )
                if payload is None:
                    continue
                result = self.plane.store.enqueue_atlas_publication(
                    payload, local_episode_id=episode_id,
                    investigation_revision_id=state.get("revision_id"),
                )
                if result.get("accepted") is False:
                    projection_error = "Pending Estima outbox quota reached"
                    continue
                self._seen[episode_id] = signature
                if result.get("is_new"):
                    queued += 1
            except Exception as error:
                projection_error = f"Estima projection unavailable ({type(error).__name__})"
        if projection_error:
            self.plane.store.set_setting("atlas_projection_last_error", projection_error)
        else:
            self.plane.store.set_setting("atlas_projection_last_error", "")
        return queued

    def _drain(self, limit: int = 4, client=None) -> dict[str, int]:
        with self._lock:
            estima = client if client is not None else self.plane.estima_client("publish")
            if estima is None:
                return {"sent": 0, "retried": 0, "failed": 0}
            sent = retried = failed = 0
            current_instance = str(self.plane._effective_estima_settings().get("instance_id") or "")
            for row in self.plane.store.due_collective_withdrawals(limit=limit):
                try:
                    if str(row["instance_id"]) != current_instance:
                        self.plane.store.defer_collective_withdrawal(
                            str(row["local_episode_id"]), "Publisher instance mismatch", 300, permanent=True,
                        )
                        failed += 1
                        continue
                    estima.delete_episode(str(row["episode_id"]))
                except EstimaClientError as error:
                    permanent = error.status_code is not None and 400 <= error.status_code < 500 and error.status_code not in {408, 425, 429}
                    attempts = int(row.get("attempts", 0)) + 1
                    self.plane.store.defer_collective_withdrawal(
                        str(row["local_episode_id"]), str(error), min(300, 2 ** min(attempts, 8)), permanent=permanent,
                    )
                    failed += 1 if permanent else 0
                    retried += 0 if permanent else 1
                except Exception as error:
                    attempts = int(row.get("attempts", 0)) + 1
                    self.plane.store.defer_collective_withdrawal(
                        str(row["local_episode_id"]), f"Estima withdrawal unavailable ({type(error).__name__})",
                        min(300, 2 ** min(attempts, 8)),
                    )
                    retried += 1
                else:
                    self.plane.store.complete_collective_withdrawal(str(row["local_episode_id"]))
                    sent += 1
            for row in self.plane.store.due_atlas_publications(limit=limit):
                try:
                    response = estima.create_case(row["payload"])
                except EstimaClientError as error:
                    status = error.status_code
                    permanent = status is not None and 400 <= status < 500 and status not in {408, 425, 429}
                    attempts = int(row.get("attempts", 0)) + 1
                    delay = min(300, 2 ** min(attempts, 8))
                    self.plane.store.defer_atlas_publication(
                        int(row["outbox_id"]), str(error), delay, permanent=permanent,
                    )
                    if permanent:
                        failed += 1
                    else:
                        retried += 1
                except Exception as error:
                    attempts = int(row.get("attempts", 0)) + 1
                    self.plane.store.defer_atlas_publication(
                        int(row["outbox_id"]), f"Estima send unavailable ({type(error).__name__})",
                        min(300, 2 ** min(attempts, 8)),
                    )
                    retried += 1
                else:
                    token = str(self.plane._effective_estima_settings().get("token") or "").strip()
                    self.plane.store.complete_atlas_publication(
                        int(row["outbox_id"]), remote_case_id=_remote_case_id(response, token),
                    )
                    sent += 1
            return {"sent": sent, "retried": retried, "failed": failed}

    def process_once(self, limit: int = 4, client=None, scan_all: bool = True) -> dict[str, int]:
        with self._lock:
            queued = self.scan_completed(None if scan_all else [])
            result = self._drain(limit=limit, client=client)
            return {"queued": queued, **result}


# Compatibility alias for callers which still inspect the previous class name.
AtlasPublisher = EstimaPublisher
