"""Background projection and durable retry worker for Atlas publication."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from fcapsule.atlas_client import AtlasClientError
from fcapsule.atlas_projection import project_atlas_case


TERMINAL_STATES = {"ready", "inconclusive", "incomplete", "not_configured"}


class AtlasPublisher:
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
            self.thread = threading.Thread(target=self._run, daemon=True, name="fcapsule-atlas-publisher")
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
            if not self.plane._effective_atlas_settings().get("publish_enabled"):
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
            self.plane.store.set_setting("atlas_projection_last_error", "Atlas completion hook unavailable")

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
                if not self.plane._effective_atlas_settings().get("publish_enabled"):
                    continue
                periodic_scan = time.monotonic() - self._last_full_scan >= 600
                with self._lock:
                    if full_scan or periodic_scan or episode_ids:
                        self.scan_completed(None if full_scan or periodic_scan else episode_ids)
                    if full_scan or periodic_scan:
                        self._last_full_scan = time.monotonic()
                    self._drain(limit=4)
            except Exception as error:
                self.plane.store.set_setting("atlas_projection_last_error", f"Atlas worker unavailable ({type(error).__name__})")

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
        config = self.plane._effective_atlas_settings()
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
                payload = project_atlas_case(
                    str(config.get("instance_id") or ""), episode, state, retained, app,
                )
                if payload is None:
                    continue
                result = self.plane.store.enqueue_atlas_publication(payload)
                if result.get("accepted") is False:
                    projection_error = "Pending Atlas outbox quota reached"
                    continue
                self._seen[episode_id] = signature
                if result.get("is_new"):
                    queued += 1
            except Exception as error:
                projection_error = f"Atlas projection unavailable ({type(error).__name__})"
        if projection_error:
            self.plane.store.set_setting("atlas_projection_last_error", projection_error)
        else:
            self.plane.store.set_setting("atlas_projection_last_error", "")
        return queued

    def _drain(self, limit: int = 4, client=None) -> dict[str, int]:
        with self._lock:
            atlas = client if client is not None else self.plane.atlas_client("publish")
            if atlas is None:
                return {"sent": 0, "retried": 0, "failed": 0}
            sent = retried = failed = 0
            for row in self.plane.store.due_atlas_publications(limit=limit):
                try:
                    atlas.create_case(row["payload"])
                except AtlasClientError as error:
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
                        int(row["outbox_id"]), f"Atlas send unavailable ({type(error).__name__})",
                        min(300, 2 ** min(attempts, 8)),
                    )
                    retried += 1
                else:
                    self.plane.store.complete_atlas_publication(int(row["outbox_id"]))
                    sent += 1
            return {"sent": sent, "retried": retried, "failed": failed}

    def process_once(self, limit: int = 4, client=None, scan_all: bool = True) -> dict[str, int]:
        with self._lock:
            queued = self.scan_completed(None if scan_all else [])
            result = self._drain(limit=limit, client=client)
            return {"queued": queued, **result}
