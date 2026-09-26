"""Application service coordinating incident capsules and stored metadata."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fcapsule.env import load_env_file, write_env_value
from fcapsule.estima_client import estima_client_from_config, estima_settings_from_env, validate_estima_url
from fcapsule.estima_publisher import EstimaPublisher
from fcapsule.evidence_service import EvidenceService
from fcapsule.incident_report import build_incident_report
from fcapsule.io.archive_writer import create_archive
from fcapsule.io.case_loader import (
    REQUIRED_FILES,
    _validate_alerts,
    _validate_configurations,
    _validate_metrics,
    load_case,
    read_case_json,
)
from fcapsule.io.output_writer import write_json
from fcapsule.live_sources import LiveSourceCoordinator
from fcapsule.investigation_service import InvestigationService
from fcapsule.pipeline import investigate_case
from fcapsule.reasoning.incident_briefing import generate_incident_briefing
from fcapsule.reasoning.llm_client import ChatRequest, DeepSeekChatClient, LLMUnavailableError, OpenRouterChatClient
from fcapsule.reasoning.openrouter import OpenRouterClient, OpenRouterError
from fcapsule.related_groups import RelatedEpisodeService
from fcapsule.store import FCAPSuleStore, utc_now
from fcapsule.ui.dashboard import render_dashboard


def _resource_identity(
    alert: dict[str, Any],
    fallback: str,
    configurations: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Identify the monitored target while keeping collector identity as evidence."""

    labels = alert.get("labels", {}) if isinstance(alert.get("labels"), dict) else {}
    annotations = alert.get("annotations", {}) if isinstance(alert.get("annotations"), dict) else {}
    alert_name = str(alert.get("alertname") or labels.get("alertname") or annotations.get("summary") or fallback)
    normalized_name = alert_name.lower()

    def label(*names: str) -> str:
        return next((str(labels[name]) for name in names if labels.get(name)), "")

    node = label("node", "kubernetes_node", "hostname", "host", "nodename")
    if not node and "node" in normalized_name:
        collector_pod = label("pod", "pod_name", "kubernetes_pod_name")
        node = next(
            (
                str(item["node"])
                for item in configurations or []
                if item.get("kind") == "PodSpec" and item.get("name") == collector_pod and item.get("node")
            ),
            "",
        )
    if not node and "node" in normalized_name:
        node = label("instance")
        if node.count(":") == 1:
            node = node.rsplit(":", 1)[0]
    if node:
        return {"kind": "node", "name": node, "alert_identity": alert_name}
    pod = label("pod", "pod_name", "kubernetes_pod_name")
    if pod:
        return {"kind": "pod", "name": pod, "alert_identity": alert_name}
    scope = alert.get("resolved_scope") if isinstance(alert.get("resolved_scope"), dict) else {}
    if scope.get("kind") in {"cnfc", "vnfc"} and scope.get("name"):
        return {"kind": str(scope["kind"]), "name": str(scope["name"]), "alert_identity": alert_name}
    workload = label("deployment", "statefulset", "daemonset", "workload", "service")
    if workload:
        return {"kind": "workload", "name": workload, "alert_identity": alert_name}
    return {"kind": "application", "name": fallback, "alert_identity": alert_name}


MEDIA_DEFAULTS = {
    "vision_model": "qwen/qwen3-vl-8b-instruct",
    "asr_model": "qwen/qwen3-asr-0.6b",
}

CORE_PROVIDER_LABELS = {"deepseek": "DeepSeek", "openrouter": "OpenRouter"}
CORE_PROVIDER_KEYS = {"deepseek": "DEEPSEEK_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
CORE_DEFAULT_MODELS = {
    "deepseek": "deepseek-v4-pro",
    "openrouter": "deepseek/deepseek-v4-pro-0813",
}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(value)))
    except (TypeError, ValueError):
        return default


def _credential_fingerprint(value: str | None) -> str:
    """Track a credential version without persisting or returning the credential."""

    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _capability_status(error: Exception) -> str:
    text = str(error).lower()
    if "401" in text or "403" in text or "invalid api" in text or "invalid key" in text:
        return "invalid_credentials"
    if "402" in text or "insufficient" in text or "credit" in text or "balance" in text:
        return "insufficient_credit"
    if "404" in text or "unsupported" in text or "not found" in text or "model" in text:
        return "unsupported_model"
    return "temporarily_unavailable"


class ControlPlane:
    """Thread-safe coordinator shared by the API and operator console."""

    def __init__(self, state_dir: str | Path = ".fcapsule") -> None:
        self.state_dir = Path(state_dir).resolve()
        self.output_root = self.state_dir / "capsules"
        self.ai_config_path = self.state_dir / "ai-settings.json"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.store = FCAPSuleStore(self.state_dir / "fcapsule.db")
        self.live_sources = LiveSourceCoordinator(self.store, self.state_dir)
        self.lock = threading.Lock()
        self.briefing_lock = threading.RLock()
        self.briefing_jobs: set[str] = set()
        self.briefing_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fcapsule-briefing")
        self.evidence = EvidenceService(self)
        self.investigator = InvestigationService(self)
        self.related_groups = RelatedEpisodeService(self)
        self.running = False
        self.active_job: str | None = None
        self.current_incident_id: str | None = None
        self.current_capsule_id: str | None = None
        self.error: str | None = None
        self.events: list[dict[str, Any]] = []
        self.phases = self._empty_phases()
        self.live: dict[str, Any] = {}
        self.source_state: dict[str, Any] = {
            "configuration": self.live_sources.configuration(),
            "targets": {},
            "last_sync_at": None,
            "pods_visible": 0,
            "applications_visible": 0,
            "active_alerts": 0,
            "error": None,
        }
        self.source_stop = threading.Event()
        self.source_monitor: threading.Thread | None = None
        self.pending_webhook_sync = False
        self._estima_config_lock = threading.RLock()
        self.estima_settings_path = self.state_dir / "estima-settings.json"
        self.legacy_atlas_settings_path = self.state_dir / "atlas-settings.json"
        load_env_file(self.state_dir / ".env")
        load_env_file()
        self.source_state["configuration"] = self.live_sources.configuration()
        self._persist_ai_settings()
        self._last_retention_check = 0.0
        self.purge_expired_incidents()
        self._refresh_existing_identities()
        existing = self.store.overview()
        if existing["incidents"]:
            incident = existing["incidents"][0]
            self.current_incident_id = incident["incident_id"]
        if existing["capsules"]:
            capsule_record = existing["capsules"][0]
            self.current_capsule_id = capsule_record["capsule_id"]
            self.phases["capsule"] = {
                "status": "done",
                "message": "Capsule ready",
                "details": {},
                "updated_at": time.time(),
            }
            output_dir = Path(capsule_record["output_dir"])
            evaluation_path = output_dir / "evaluation.json"
            if evaluation_path.is_file():
                self.live["evaluation"] = json.loads(evaluation_path.read_text(encoding="utf-8"))
                self.live["selected_evidence"] = capsule_record["selected_evidence"]
        self.estima_publisher = EstimaPublisher(self)
        self.atlas_publisher = self.estima_publisher
        if self._effective_estima_settings().get("publish_enabled"):
            self.estima_publisher.start()

    def _read_estima_settings(self) -> dict[str, Any]:
        migrate_legacy = not self.estima_settings_path.exists()
        source = self.legacy_atlas_settings_path if migrate_legacy else self.estima_settings_path
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(value, dict):
            return {}
        if migrate_legacy:
            # Copy the legacy configuration forward; retain the old file for rollback.
            self._write_estima_settings(value)
        return value

    def _write_estima_settings(self, value: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.estima_settings_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, self.estima_settings_path)
        try:
            self.estima_settings_path.chmod(0o600)
        except OSError:
            pass

    def _effective_estima_settings(self) -> dict[str, Any]:
        with self._estima_config_lock:
            config = estima_settings_from_env()
            stored = self._read_estima_settings()
            for key in ("url", "token", "instance_id", "read_enabled", "publish_enabled"):
                if key in stored:
                    if key == "instance_id" and config.get("instance_id") and stored.get("instance_id_auto"):
                        continue
                    config[key] = stored[key]
            if not config.get("instance_id"):
                config["instance_id"] = "fcapsule-" + str(uuid.uuid4())
                stored["instance_id"] = config["instance_id"]
                stored["instance_id_auto"] = True
                self._write_estima_settings(stored)
            return config

    def _effective_atlas_settings(self) -> dict[str, Any]:
        """Compatibility alias for settings integrations from before the rename."""
        return self._effective_estima_settings()

    def estima_client(self, operation: str):
        """Return the optional shared Estima client when the operation is enabled."""
        try:
            return estima_client_from_config(self._effective_estima_settings(), operation)
        except ValueError as error:
            self.store.set_setting("atlas_client_config_error", str(error)[:160])
            return None

    def atlas_client(self, operation: str):
        """Compatibility alias for callers using the previous integration name."""
        return self.estima_client(operation)

    def estima_configuration(self) -> dict[str, Any]:
        config = self._effective_estima_settings()
        status = self.store.atlas_outbox_status()
        return {
            "url": str(config.get("url") or ""),
            "instance_id": str(config.get("instance_id") or ""),
            "token_configured": bool(str(config.get("token") or "").strip()),
            "read_enabled": bool(config.get("read_enabled")),
            "publish_enabled": bool(config.get("publish_enabled")),
            "pending_count": status["pending_count"],
            "failed_count": status["failed_count"],
            "last_error": status["last_error"] or self.store.get_setting("atlas_projection_last_error")
                          or self.store.get_setting("atlas_client_config_error"),
        }

    def atlas_configuration(self) -> dict[str, Any]:
        """Compatibility alias for callers using the previous settings name."""
        return self.estima_configuration()

    def update_estima_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Collective settings must be an object")
        with self._estima_config_lock:
            stored = self._read_estima_settings()
            env = estima_settings_from_env()
            current = {**env, **stored}
            old_url = str(current.get("url") or "")
            old_token = str(current.get("token") or "")
            url = str(payload.get("url", current.get("url") or "")).strip()
            if payload.get("clear_url") is True:
                stored.pop("url", None)
            elif url:
                validate_estima_url(url)
                stored["url"] = url
            instance_id = str(payload.get("instance_id", current.get("instance_id") or "")).strip()
            if not instance_id:
                raise ValueError("Collective instance ID must be non-empty")
            if len(instance_id) > 96 or not all(character.isalnum() or character in "._:-" for character in instance_id):
                raise ValueError("Collective instance ID contains unsupported characters")
            stored["instance_id"] = instance_id
            stored["instance_id_auto"] = False
            if payload.get("clear_token") is True:
                stored["token"] = ""
            elif isinstance(payload.get("token"), str) and payload["token"].strip():
                token = payload["token"].strip()
                if len(token) > 4096 or "\n" in token or "\r" in token:
                    raise ValueError("Collective service token is invalid")
                stored["token"] = token
            for key in ("read_enabled", "publish_enabled"):
                if key in payload:
                    value = payload[key]
                    if isinstance(value, bool):
                        stored[key] = value
                    elif isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0", "on", "off"}:
                        stored[key] = value.strip().lower() in {"true", "1", "on"}
                    else:
                        raise ValueError(f"{key} must be a boolean")
            self._write_estima_settings(stored)
        updated = self._effective_estima_settings()
        if str(updated.get("url") or "") != old_url or str(updated.get("token") or "") != old_token:
            self.store.retry_failed_atlas_publications(auth_only=True)
        if updated.get("publish_enabled"):
            self.estima_publisher.start()
            self.estima_publisher.request_full_scan()
        else:
            self.estima_publisher.shutdown(drain=False)
        return self.estima_configuration()

    def update_atlas_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Compatibility alias for callers using the previous settings name."""
        return self.update_estima_configuration(payload)

    def process_estima_outbox_once(self, limit: int = 4, client=None) -> dict[str, int]:
        return self.estima_publisher.process_once(limit=limit, client=client, scan_all=True)

    def process_atlas_outbox_once(self, limit: int = 4, client=None) -> dict[str, int]:
        """Compatibility alias for the legacy outbox operation name."""
        return self.process_estima_outbox_once(limit=limit, client=client)

    def retry_estima_publications(self, limit: int = 100) -> int:
        retried = self.store.retry_failed_atlas_publications(limit=limit)
        if retried and self._effective_estima_settings().get("publish_enabled"):
            self.estima_publisher.start()
            self.estima_publisher.wake()
        return retried

    def retry_atlas_publications(self, limit: int = 100) -> int:
        """Compatibility alias for persisted Atlas outbox records."""
        return self.retry_estima_publications(limit=limit)

    def _refresh_existing_identities(self) -> None:
        """Backfill retained cases when new target identity fields are introduced."""

        for incident in self.store.list_incidents(limit=10000) + self.store.list_incidents(limit=10000, archived=True):
            try:
                case_dir = Path(incident["case_dir"])
                if not case_dir.is_dir() or any(not (case_dir / name).is_file() for name in REQUIRED_FILES):
                    continue
                alerts = _validate_alerts(read_case_json(case_dir / "alert.json"))
                configurations_path = case_dir / "kubernetes_config.json"
                configurations = (
                    _validate_configurations(read_case_json(configurations_path))
                    if configurations_path.is_file()
                    else []
                )
                if alerts:
                    identity = _resource_identity(alerts[0], str(incident["app_id"]), configurations)
                    self.store.update_incident_identity(
                        str(incident["incident_id"]), identity["kind"], identity["name"], identity["alert_identity"]
                    )
            except (FileNotFoundError, KeyError, ValueError, OSError):
                continue

    @staticmethod
    def _empty_phases() -> dict[str, dict[str, Any]]:
        return {"capsule": {"status": "waiting", "message": "Waiting"}}

    def _capability(
        self, setting_key: str, credential: str | None, model: str, provider: str | None = None,
    ) -> dict[str, Any]:
        """Return a capability state only when it matches the active provider/key/model."""

        if not credential:
            return {"status": "not_configured", "last_checked_at": None, "message": "No local credential is configured."}
        raw = self.store.get_setting(setting_key, "") or ""
        try:
            saved = json.loads(raw)
        except json.JSONDecodeError:
            saved = {}
        if (
            not isinstance(saved, dict)
            or saved.get("credential_fingerprint") != _credential_fingerprint(credential)
            or saved.get("model") != model
            or (provider is not None and saved.get("provider", "deepseek") != provider)
        ):
            return {"status": "not_validated", "last_checked_at": None, "message": "Validate this credential and model before enabling the capability."}
        return {
            "status": str(saved.get("status") or "not_validated"),
            "last_checked_at": saved.get("last_checked_at"),
            "message": str(saved.get("message") or ""),
            "usage": saved.get("usage") if isinstance(saved.get("usage"), dict) else {},
        }

    def _set_capability(
        self, setting_key: str, credential: str, model: str, status: str, message: str,
        usage: dict[str, Any] | None = None, provider: str | None = None,
    ) -> dict[str, Any]:
        value = {
            "credential_fingerprint": _credential_fingerprint(credential),
            "model": model,
            "status": status,
            "message": message[:300],
            "last_checked_at": utc_now(),
            "usage": usage or {},
        }
        if provider is not None:
            value["provider"] = provider
        self.store.set_setting(setting_key, json.dumps(value, ensure_ascii=True, separators=(",", ":")))
        return self._capability(setting_key, credential, model, provider)

    def ai_configuration(self) -> dict[str, Any]:
        """Return local AI settings without ever returning a credential."""

        profiles = self.store.list_model_profiles()
        configured_provider = self.store.get_setting("ai_provider")
        provider = str(configured_provider or os.environ.get("FCAPSULE_LLM_PROVIDER") or "deepseek").strip().lower()
        if provider not in CORE_PROVIDER_KEYS:
            raise ValueError("FCAPSULE_LLM_PROVIDER must be deepseek or openrouter")
        default_model_id = CORE_DEFAULT_MODELS[provider]
        default = next((item for item in profiles if item["model_id"] == default_model_id), profiles[0])
        saved_model = self.store.get_setting("ai_active_model")
        if configured_provider is None and saved_model:
            saved_profile = next((item for item in profiles if item["model_id"] == saved_model), None)
            if saved_profile and saved_profile["provider"] != provider:
                saved_model = None
        model = saved_model or str(default["model_id"])
        max_tokens = _bounded_int(self.store.get_setting("ai_max_tokens", str(default["max_tokens"])), int(default["max_tokens"]), 256, 6000)
        maximum_total_tokens = _bounded_int(self.store.get_setting("ai_max_total_tokens", "12000"), 12000, 4000, 100000)
        maximum_prompt_tokens = _bounded_int(self.store.get_setting("ai_max_prompt_tokens", "3200"), 3200, 1600, 12000)
        maximum_checks = _bounded_int(self.store.get_setting("ai_max_checks", "1"), 1, 0, 4)
        credential = os.environ.get(CORE_PROVIDER_KEYS[provider])
        return {
            "provider": provider,
            "providers": [{"id": key, "label": label} for key, label in CORE_PROVIDER_LABELS.items()],
            "model": model,
            "max_tokens": max_tokens,
            "max_total_tokens": maximum_total_tokens,
            "max_prompt_tokens": maximum_prompt_tokens,
            "max_checks": maximum_checks,
            "api_key_configured": bool(credential),
            "capability": self._capability("ai_core_capability", credential, model, provider),
            "models": profiles,
            "config_path": str(self.ai_config_path),
        }

    def media_configuration(self) -> dict[str, Any]:
        """Expose selected specialist models and validation states without secrets."""

        vision_model = self.store.get_setting("media_vision_model", MEDIA_DEFAULTS["vision_model"]) or MEDIA_DEFAULTS["vision_model"]
        asr_model = self.store.get_setting("media_asr_model", MEDIA_DEFAULTS["asr_model"]) or MEDIA_DEFAULTS["asr_model"]
        credential = os.environ.get("OPENROUTER_API_KEY")
        ai = self.ai_configuration()
        core = ai["capability"]
        return {
            "provider": "openrouter",
            "api_key_configured": bool(credential),
            "vision": {"model": vision_model, "capability": self._capability("media_vision_capability", credential, vision_model)},
            "audio": {"model": asr_model, "capability": self._capability("media_audio_capability", credential, asr_model)},
            "core_investigator": {"provider": ai["provider"], "model": ai["model"], "capability": core},
        }

    def media_submission_allowed(self, kind: str) -> tuple[bool, str]:
        if kind == "text":
            if self.ai_configuration()["capability"]["status"] != "ready":
                return False, "Validate the core investigator before adding text context."
            return True, ""
        media = self.media_configuration()
        core_status = media["core_investigator"]["capability"]["status"]
        specialist = media.get(kind, {}).get("capability", {}).get("status")
        if core_status != "ready":
            return False, "Validate the core investigator before adding media evidence."
        if specialist != "ready":
            return False, f"Validate the selected {kind} model before adding media evidence."
        return True, ""

    def core_chat_client(self, provider: str | None = None, timeout_seconds: int = 90, api_key: str | None = None):
        """Build the selected core provider client without any implicit fallback."""

        selected = provider or self.ai_configuration()["provider"]
        if selected == "deepseek":
            return DeepSeekChatClient(api_key=api_key, timeout_seconds=timeout_seconds)
        if selected == "openrouter":
            return OpenRouterChatClient(api_key=api_key, timeout_seconds=timeout_seconds)
        raise ValueError("provider must be deepseek or openrouter")

    def _validate_core(
        self, model: str, credential: str, provider: str = "deepseek", *, persist: bool = True,
    ) -> dict[str, Any]:
        def record(status: str, message: str, usage: dict[str, Any] | None = None) -> dict[str, Any]:
            if persist:
                return self._set_capability(
                    "ai_core_capability", credential, model, status, message, usage, provider=provider,
                )
            return {"status": status, "message": message[:300], "usage": usage or {}}

        try:
            result = self.core_chat_client(provider, timeout_seconds=35, api_key=credential).chat(
                ChatRequest(
                    model=model,
                    messages=[{"role": "user", "content": "Return the JSON object {\"status\":\"ok\"}."}],
                    max_tokens=96,
                    temperature=0,
                    json_output=True,
                )
            )
            if not str(result.get("content", "")).strip():
                raise LLMUnavailableError("Provider returned no usable validation output")
        except (LLMUnavailableError, OSError, ValueError) as error:
            return record(_capability_status(error), str(error))
        return record("ready", "Core model accepted a bounded JSON canary.", result.get("usage"))

    def validate_ai_configuration(self) -> dict[str, Any]:
        config = self.ai_configuration()
        credential = os.environ.get(CORE_PROVIDER_KEYS[config["provider"]])
        if not credential:
            return config
        self._validate_core(str(config["model"]), credential, str(config["provider"]))
        self._persist_ai_settings()
        return self.ai_configuration()

    def _persist_ai_settings(self) -> None:
        config = self.ai_configuration()
        media = self.media_configuration()
        # The local config makes active runtime choices explicit; secrets stay in .env only.
        write_json(
            self.ai_config_path,
            {
                "provider": config["provider"],
                "model": config["model"],
                "max_tokens": config["max_tokens"],
                "max_total_tokens": config["max_total_tokens"],
                "max_prompt_tokens": config["max_prompt_tokens"],
                "max_checks": config["max_checks"],
                "media": {"provider": media["provider"], "vision_model": media["vision"]["model"], "asr_model": media["audio"]["model"]},
            },
        )

    def update_ai_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Update bounded investigator settings; verify a replacement key before saving it."""

        current = self.ai_configuration()
        provider = str(payload.get("provider", current["provider"])).strip().lower()
        if provider not in CORE_PROVIDER_KEYS:
            raise ValueError("Provider must be deepseek or openrouter")
        target_key_was_configured = bool(os.environ.get(CORE_PROVIDER_KEYS[provider]))
        model_default = CORE_DEFAULT_MODELS[provider] if provider != current["provider"] else current["model"]
        model = str(payload.get("model", model_default)).strip()
        if not model or any(character.isspace() for character in model):
            raise ValueError("Model ID must be a non-empty identifier without spaces")
        max_tokens = _bounded_int(payload.get("max_tokens", current["max_tokens"]), current["max_tokens"], 256, 6000)
        maximum_total_tokens = _bounded_int(payload.get("max_total_tokens", current["max_total_tokens"]), current["max_total_tokens"], 4000, 100000)
        maximum_prompt_tokens = _bounded_int(payload.get("max_prompt_tokens", current["max_prompt_tokens"]), current["max_prompt_tokens"], 1600, 12000)
        maximum_checks = _bounded_int(payload.get("max_checks", current["max_checks"]), current["max_checks"], 0, 4)
        api_key = str(payload.get("api_key", "")).strip()
        if api_key and len(api_key) < 12:
            raise ValueError("API key appears too short")
        if api_key:
            validation = self._validate_core(model, api_key, provider, persist=False)
            if validation["status"] != "ready":
                raise ValueError(f"Replacement credential was not saved: {validation['message']}")
            write_env_value(self.state_dir / ".env", CORE_PROVIDER_KEYS[provider], api_key)
        self.store.upsert_model_profile(model, provider, True, max_tokens)
        self.store.set_setting("ai_provider", provider)
        self.store.set_setting("ai_active_model", model)
        self.store.set_setting("ai_max_tokens", str(max_tokens))
        self.store.set_setting("ai_max_total_tokens", str(maximum_total_tokens))
        self.store.set_setting("ai_max_prompt_tokens", str(maximum_prompt_tokens))
        self.store.set_setting("ai_max_checks", str(maximum_checks))
        if api_key:
            self._set_capability(
                "ai_core_capability", api_key, model, "ready", validation["message"],
                validation.get("usage"), provider=provider,
            )
        if not api_key and (model != current["model"] or provider != current["provider"]):
            self.store.set_setting("ai_core_capability", "")
        self._persist_ai_settings()
        # Provider/model edits alone must not turn retained backlog into model jobs.
        # Adding a validated key is an explicit unblock action for work held without that provider's key.
        if api_key and not target_key_was_configured:
            self.investigator.resume()
        return self.ai_configuration()

    def update_media_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Save selected specialist models and validate a candidate key before persisting it."""

        current = self.media_configuration()
        vision_model = str(payload.get("vision_model", current["vision"]["model"])).strip()
        asr_model = str(payload.get("asr_model", current["audio"]["model"])).strip()
        if not vision_model or not asr_model or any(character.isspace() for character in (vision_model, asr_model)):
            raise ValueError("Media model IDs must be non-empty identifiers without spaces")
        candidate = str(payload.get("api_key", "")).strip()
        if candidate and len(candidate) < 12:
            raise ValueError("API key appears too short")
        self.store.set_setting("media_vision_model", vision_model)
        self.store.set_setting("media_asr_model", asr_model)
        if not candidate and (vision_model != current["vision"]["model"] or asr_model != current["audio"]["model"]):
            self.store.set_setting("media_vision_capability", "")
            self.store.set_setting("media_audio_capability", "")
        if candidate:
            result = self._validate_media(vision_model, asr_model, candidate)
            if not any(item["capability"]["status"] == "ready" for item in (result["vision"], result["audio"])):
                raise ValueError("Replacement credential was not saved because no media capability could be validated")
            write_env_value(self.state_dir / ".env", "OPENROUTER_API_KEY", candidate)
        self._persist_ai_settings()
        return self.media_configuration()

    def _validate_media(self, vision_model: str, asr_model: str, credential: str) -> dict[str, Any]:
        client = OpenRouterClient(api_key=credential, timeout_seconds=45)
        for setting, model, operation, label in (
            ("media_vision_capability", vision_model, client.validate_vision, "Image model accepted a minimal visual canary."),
            ("media_audio_capability", asr_model, client.validate_asr, "Audio model accepted a minimal audio canary."),
        ):
            try:
                result = operation(model)
                self._set_capability(setting, credential, model, "ready", label, result.get("usage"))
            except (OpenRouterError, OSError, ValueError) as error:
                self._set_capability(setting, credential, model, _capability_status(error), str(error))
        return {
            "vision": {"model": vision_model, "capability": self._capability("media_vision_capability", credential, vision_model)},
            "audio": {"model": asr_model, "capability": self._capability("media_audio_capability", credential, asr_model)},
        }

    def validate_media_configuration(self) -> dict[str, Any]:
        config = self.media_configuration()
        credential = os.environ.get("OPENROUTER_API_KEY")
        if not credential:
            return config
        self._validate_media(str(config["vision"]["model"]), str(config["audio"]["model"]), credential)
        self._persist_ai_settings()
        return self.media_configuration()

    def general_configuration(self) -> dict[str, Any]:
        try:
            retention_days = int(self.store.get_setting("incident_retention_days", "30") or 30)
        except ValueError:
            retention_days = 30
        return {"incident_retention_days": min(3650, max(1, retention_days))}

    def update_general_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        retention_days = int(payload.get("incident_retention_days", 30))
        if retention_days < 1 or retention_days > 3650:
            raise ValueError("Incident retention must be between 1 and 3650 days")
        self.store.set_setting("incident_retention_days", str(retention_days))
        self.purge_expired_incidents(force=True)
        return self.general_configuration()

    def submit_evidence(self, episode_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        attachment = self.evidence.submit(episode_id, payload)
        self.refresh_evidence_exports(episode_id)
        return attachment

    def correct_evidence(self, attachment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        attachment = self.evidence.correct(attachment_id, payload)
        record = self.store.get_evidence_attachment(attachment_id)
        if record:
            self.refresh_evidence_exports(str(record["episode_id"]))
        return attachment

    def remove_evidence(self, attachment_id: str) -> None:
        record = self.store.get_evidence_attachment(attachment_id)
        if not record:
            raise KeyError("Evidence attachment not found")
        episode_id = str(record["episode_id"])
        self.evidence.remove(attachment_id)
        self.refresh_evidence_exports(episode_id)

    def refresh_evidence_exports(self, episode_id: str) -> None:
        """Update manifests only; raw uploaded media is never added to the default archive."""

        manifest = self.evidence.manifest(episode_id)
        episode = self.store.get_episode(episode_id)
        if not episode:
            return
        for signal in episode["signals"]:
            record = self.store.get_capsule_for_incident(str(signal["incident_id"]))
            if not record:
                continue
            root = Path(record["output_dir"])
            write_json(root / "evidence_manifest.json", {"episode_id": episode_id, "generated_at": utc_now(), "attachments": manifest})
            create_archive(root, str(signal["incident_id"]))

    def update_investigation_with_evidence(self, episode_id: str) -> dict[str, Any]:
        ready = [item for item in self.store.list_evidence_attachments(episode_id) if item["status"] == "ready"]
        if not ready:
            raise ValueError("No processed evidence is ready to update this investigation")
        return self.investigator.start(episode_id, retry=True, reason="evidence_added")

    def start_source_disconnected_review(self, episode_id: str, question: str) -> dict[str, Any]:
        return self.investigator.start_source_disconnected_review(episode_id, question)

    def separate_related_episode(self, group_id: str, episode_id: str) -> dict[str, Any]:
        """Record an operator correction so an automatic correlation is not restored."""

        return self.store.separate_related_episode(group_id, episode_id)

    def set_incident_archived(self, incident_id: str, archived: bool) -> dict[str, Any]:
        incident = self.store.set_incident_archived(incident_id, archived)
        if archived and self.current_incident_id == incident_id:
            active = self.store.list_incidents(limit=1)
            self.current_incident_id = active[0]["incident_id"] if active else None
        return incident

    def set_episode_archived(self, episode_id: str, archived: bool) -> dict[str, Any]:
        episode = self.store.set_episode_archived(episode_id, archived)
        if archived and self.current_incident_id in self.store.episode_incident_ids(episode_id):
            active = self.store.list_incidents(limit=1)
            self.current_incident_id = active[0]["incident_id"] if active else None
        return episode

    def delete_episode(self, episode_id: str) -> None:
        incident_ids = self.store.episode_incident_ids(episode_id)
        if not incident_ids:
            raise KeyError(f"Unknown episode: {episode_id}")
        for attachment in self.store.list_evidence_attachments(episode_id):
            self.evidence.remove(str(attachment["attachment_id"]))
        for incident_id in incident_ids:
            self.delete_incident(incident_id)

    def delete_incident(self, incident_id: str) -> None:
        incident = self.store.get_incident(incident_id)
        if not incident:
            raise KeyError(f"Unknown incident: {incident_id}")
        capsule = self.store.get_capsule_for_incident(incident_id)
        episode = self.store.episode_for_incident(incident_id)
        with self.briefing_lock:
            if episode and len(self.store.episode_incident_ids(str(episode["episode_id"]))) == 1:
                for attachment in self.store.list_evidence_attachments(str(episode["episode_id"])):
                    self.evidence.remove(str(attachment["attachment_id"]))
            self.store.delete_incident(incident_id)
            self._remove_managed_tree(incident.get("case_dir"))
            if capsule:
                self._remove_managed_tree(capsule.get("output_dir"))
            if episode:
                self.investigator.invalidate(episode["episode_id"])
        if self.current_incident_id == incident_id:
            active = self.store.list_incidents(limit=1)
            self.current_incident_id = active[0]["incident_id"] if active else None
        if capsule and self.current_capsule_id == capsule.get("capsule_id"):
            self.current_capsule_id = None

    def purge_expired_incidents(self, force: bool = False) -> int:
        now = time.monotonic()
        if not force and now - self._last_retention_check < 60:
            return 0
        self._last_retention_check = now
        retention_days = self.general_configuration()["incident_retention_days"]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat().replace("+00:00", "Z")
        expired = self.store.incidents_older_than(cutoff)
        for incident in expired:
            self.delete_incident(str(incident["incident_id"]))
        return len(expired)

    def _remove_managed_tree(self, value: Any) -> None:
        if not value:
            return
        path = Path(str(value)).resolve()
        try:
            path.relative_to(self.state_dir)
        except ValueError:
            return
        if path.is_dir():
            shutil.rmtree(path)

    def source_configuration(self) -> dict[str, Any]:
        return self.live_sources.configuration()

    def update_source_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = self.live_sources.update_configuration(payload)
        with self.lock:
            self.source_state["configuration"] = config
        if config["enabled"]:
            self.start_live_monitoring()
        else:
            self.stop_live_monitoring()
        return config

    def test_source_connections(self) -> dict[str, Any]:
        result = self.live_sources.test_connections()
        with self.lock:
            self.source_state.update(result)
            self.source_state["error"] = None if result["ok"] else "One or more source connections failed"
        return result

    def receive_grafana_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.live_sources.receive_grafana_alerts(payload)
        with self.lock:
            self.source_state["configuration"] = self.live_sources.configuration()
        if not self.start_source_sync():
            with self.lock:
                self.pending_webhook_sync = True
        return result

    def start_live_monitoring(self) -> None:
        if self.source_monitor and self.source_monitor.is_alive():
            return
        if not self.source_configuration()["enabled"]:
            return
        self.source_stop.clear()
        self.source_monitor = threading.Thread(target=self._monitor_sources, daemon=True, name="fcapsule-source-monitor")
        self.source_monitor.start()

    def stop_live_monitoring(self) -> None:
        self.source_stop.set()
        if self.source_monitor and self.source_monitor.is_alive():
            self.source_monitor.join(timeout=3)

    def _monitor_sources(self) -> None:
        while not self.source_stop.is_set():
            if not self.source_configuration()["enabled"]:
                return
            self.purge_expired_incidents()
            self.start_source_sync()
            interval = self.source_configuration()["poll_interval_seconds"]
            self.source_stop.wait(interval)

    def start_source_sync(self) -> bool:
        if not self._begin("sources"):
            return False
        thread = threading.Thread(target=self._run_source_sync, daemon=True, name="fcapsule-source-sync")
        thread.start()
        return True

    def _run_source_sync(self) -> None:
        captured: list[dict[str, Any]] = []
        incident_ids: list[str] = []
        try:
            self._event("sources", "running", "Discovering workloads and checking telemetry coverage")
            result = self.live_sources.synchronize()
            captured = list(result.pop("captured", []))
            active_incident_ids = set(result.pop("active_incident_ids", []))
            with self.lock:
                self.source_state.update(result)
                self.source_state["error"] = None
            for item in captured:
                incident = self.ingest_case(
                    item["case_dir"],
                    item["app_id"],
                    item["app_name"],
                    self.source_configuration()["environment"],
                    source_kind="live",
                )
                incident_ids.append(str(incident["incident_id"]))
            self.store.reconcile_live_incidents(active_incident_ids, str(result["last_sync_at"]))
            if incident_ids and self.source_configuration()["auto_build_reports"]:
                for incident_id in incident_ids:
                    self._build_capsule(incident_id)
            self._event(
                "sources",
                "done",
                "Source inventory synchronized",
                {"pods_visible": result["pods_visible"], "new_incidents": len(captured)},
                update_live=False,
            )
            self._finish()
        except Exception as exc:  # pragma: no cover - surfaced through API and UI
            with self.lock:
                self.source_state["error"] = str(exc)
                self.source_state["last_sync_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._finish(exc)

    def ingest_case(
        self,
        case_dir: str | Path,
        app_id: str,
        app_name: str | None = None,
        environment: str = "development",
        source_kind: str = "external",
    ) -> dict[str, Any]:
        """Register an externally captured normalized incident without copying raw telemetry."""

        bundle = load_case(case_dir)
        metadata = bundle.metadata
        if not self.store.get_application(app_id):
            self.store.upsert_application(
                app_id,
                app_name or str(metadata["service"]),
                str(metadata["namespace"]),
                str(metadata["cluster"]),
                environment,
                source_config={
                    "faults": {"adapter": "external", "status": "connected"},
                    "metrics": {"adapter": "external", "status": "connected"},
                    "logs": {"adapter": "external", "status": "connected"},
                    "traces": {"adapter": "on_demand", "status": "available", "retain_raw_spans": False},
                },
            )
        first_alert = bundle.alerts[0]
        annotation = first_alert.get("annotations", {}) if isinstance(first_alert.get("annotations"), dict) else {}
        identity = _resource_identity(first_alert, app_name or str(metadata.get("service", app_id)), bundle.configurations)
        raw_bytes = sum(
            path.stat().st_size
            for path in bundle.case_dir.iterdir()
            if path.is_file() and path.name in {"alert.json", "prometheus_metrics.json", "opensearch_logs.json"}
        )
        incident = self.store.record_incident(
            {
                "incident_id": bundle.case_id,
                "app_id": app_id,
                "scenario": str(metadata.get("case_title", bundle.case_id)),
                "status": str(first_alert.get("status", "firing")),
                "severity": str(first_alert.get("severity", "warning")),
                "started_at": str(first_alert.get("startsAt", metadata["window"]["start"])),
                "ended_at": first_alert.get("endsAt") if str(first_alert.get("status", "firing")).lower() == "resolved" else None,
                "case_dir": bundle.case_dir,
                "alert_count": len(bundle.alerts),
                "log_count": len(bundle.logs),
                "metric_series_count": len(bundle.metrics),
                "raw_bytes": raw_bytes,
                "trace_access": metadata.get("trace_access", {"available": False, "raw_spans_retained": False}),
                "summary": str(annotation.get("summary") or metadata["case_title"]),
                "source_kind": source_kind,
                "resource_kind": identity["kind"],
                "resource_name": identity["name"],
                "alert_identity": identity["alert_identity"],
            }
        )
        with self.lock:
            self.current_incident_id = incident["incident_id"]
        self._event("capsule", "waiting", "Incident captured; report not built", {"incident_id": incident["incident_id"]}, update_live=False)
        return incident

    def _begin(self, job: str) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.active_job = job
            self.error = None
            self.events.append({"time": time.time(), "phase": job, "status": "running", "message": f"{job.title()} started", "details": {}})
            return True

    def _finish(self, error: Exception | None = None) -> None:
        with self.lock:
            self.running = False
            self.active_job = None
            if error:
                self.error = str(error)
                self.events.append({"time": time.time(), "phase": "system", "status": "error", "message": str(error), "details": {}})
            self.events = self.events[-160:]
            pending_webhook_sync = self.pending_webhook_sync
            self.pending_webhook_sync = False
        if pending_webhook_sync:
            self.start_source_sync()

    def _event(
        self,
        phase: str,
        status: str,
        message: str,
        details: dict[str, Any] | None = None,
        update_live: bool = True,
    ) -> None:
        details = details or {}
        with self.lock:
            self.phases[phase] = {"status": status, "message": message, "details": details, "updated_at": time.time()}
            self.events.append({"time": time.time(), "phase": phase, "status": status, "message": message, "details": details})
            self.events = self.events[-160:]
            if update_live:
                self.live.update(details)

    def _pipeline_progress(self, status: str, message: str, details: dict[str, Any]) -> None:
        self._event("capsule", "done" if status == "done" else "running", message, details, update_live=False)

    def start_capsule(self, incident_id: str | None = None) -> bool:
        incident_id = incident_id or self.current_incident_id
        if not incident_id:
            raise ValueError("No incident is selected")
        if not self._begin("capsule"):
            return False
        with self.lock:
            self.phases["capsule"] = {"status": "running", "message": "Loading incident evidence"}
        thread = threading.Thread(target=self._run_capsule, args=(incident_id,), daemon=True)
        thread.start()
        return True

    def _run_capsule(self, incident_id: str) -> None:
        try:
            self._build_capsule(incident_id)
            self._finish()
        except Exception as exc:  # pragma: no cover - surfaced through API and UI
            self._finish(exc)

    def _build_capsule(self, incident_id: str) -> dict[str, Any]:
        incident = self.store.get_incident(incident_id)
        if not incident:
            raise ValueError(f"Unknown incident: {incident_id}")
        output_dir = self.output_root / incident_id
        result = investigate_case(incident["case_dir"], output_dir, self._pipeline_progress)
        render_dashboard(output_dir)
        evaluation = result["evaluation"]
        capsule_id = f"capsule-{incident_id}"
        capsule_path = output_dir / "capsule.json"
        capsule_data = json.loads(capsule_path.read_text(encoding="utf-8"))
        source_metrics = _validate_metrics(read_case_json(Path(incident["case_dir"]) / "prometheus_metrics.json"))
        write_json(output_dir / "incident_report.json", build_incident_report(capsule_data, incident, source_metrics))
        archive = create_archive(output_dir, incident_id)
        capsule = self.store.record_capsule(
            {
                "capsule_id": capsule_id,
                "incident_id": incident_id,
                "app_id": incident["app_id"],
                "output_dir": output_dir,
                "archive_path": archive,
                "size_bytes": capsule_path.stat().st_size,
                "selected_evidence": result["selected_evidence"],
                "compression": evaluation["log_compression_ratio"],
                "signal_preservation": evaluation["important_signal_preservation"],
                "grounding": evaluation["hypothesis_grounding_score"],
                "runtime_seconds": evaluation["runtime_seconds"],
                "model_winner": None,
            }
        )
        with self.lock:
            self.current_capsule_id = capsule_id
            self.live.update(
                {
                    "capsule": capsule,
                    "evaluation": evaluation,
                    "selected_evidence": result["selected_evidence"],
                }
            )
        self._event(
            "capsule",
            "done",
            "Capsule ready",
            {
                "selected_evidence": result["selected_evidence"],
                "compression": evaluation["log_compression_ratio"],
                "signal_preservation": evaluation["important_signal_preservation"],
            },
        )
        self.investigator.start_by_incident(incident_id)
        return capsule

    def snapshot(self) -> dict[str, Any]:
        self.purge_expired_incidents()
        try:
            related_groups = self.related_groups.refresh()
        except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError, ValueError):
            # Correlation is an optional queue cue; it must not make Operations unreadable.
            related_groups = self.store.list_related_episode_groups()
        with self.lock:
            state = {
                "running": self.running,
                "active_job": self.active_job,
                "current_incident_id": self.current_incident_id,
                "current_capsule_id": self.current_capsule_id,
                "error": self.error,
                "events": json.loads(json.dumps(self.events)),
                "phases": json.loads(json.dumps(self.phases)),
                "live": json.loads(json.dumps(self.live)),
                "ai": self.ai_configuration(),
                "media": self.media_configuration(),
                "settings": self.general_configuration(),
                "sources": json.loads(json.dumps(self.source_state)),
            }
        state["overview"] = self.store.overview()
        state["overview"]["related_groups"] = related_groups
        for key in ("episodes", "archived_episodes"):
            for episode in state["overview"].get(key, []):
                investigation = self.investigator.read(episode["episode_id"])
                episode["investigation"] = {"status": investigation["status"],
                    "completed_checks": sum(check["status"] == "completed" for check in investigation.get("checks", [])),
                    "finished_at": investigation.get("finished_at"),
                    "historical_comparison": (investigation.get("assessment") or {}).get("historical_comparison")}
        state["overview"]["triage"] = self._operations_triage(state["overview"])
        return state

    @staticmethod
    def _operations_triage(overview: dict[str, Any]) -> list[dict[str, Any]]:
        """Keep the top of Operations short and tied to an episode action."""

        severity_rank = {"critical": 2, "warning": 1, "info": 0}
        rows: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
        seen_patterns: set[str] = set()
        for episode in overview.get("episodes", []):
            recurrence = episode.get("recurrence", {})
            comparison = (episode.get("investigation", {}) or {}).get("historical_comparison") or {}
            base = {"episode_id": episode["episode_id"], "reference": episode.get("reference"),
                    "title": episode["title"], "resource": episode.get("resource", {}),
                    "started_at": episode["started_at"]}
            rank = severity_rank.get(str(episode.get("severity", "info")).lower(), 0)
            if episode.get("status") == "active":
                rows.append(((3, rank, str(episode["last_activity_at"])), {
                    **base, "kind": "active", "label": "Active incident",
                    "detail": "An alert is still firing and needs a current investigation.",
                }))
                continue
            if comparison.get("status") == "changed_or_different":
                rows.append(((2, rank, str(episode["last_activity_at"])), {
                    **base, "kind": "changed", "label": "Changed from prior occurrence",
                    "detail": str(comparison.get("summary", "Retained evidence differs from a prior episode.")),
                }))
                continue
            pattern_id = recurrence.get("pattern_id")
            if recurrence.get("previous_count", 0) and pattern_id not in seen_patterns:
                rows.append(((1, rank, str(episode["last_activity_at"])), {
                    **base, "kind": "recurring", "label": "Recurring issue",
                    "detail": f"Observed {recurrence['previous_count']} earlier time{'s' if recurrence['previous_count'] != 1 else ''} in retained history.",
                }))
                seen_patterns.add(str(pattern_id))
        rows.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in rows[:3]]

    def capsule_artifact_payload(self, capsule_id: str) -> dict[str, Any] | None:
        """Return capsule metadata and its path without loading the capsule document."""

        record = self.store.get_capsule(capsule_id)
        if not record:
            return None
        path = Path(record["output_dir"]) / "capsule.json"
        if not path.is_file():
            return None
        comparison_path = Path(record["output_dir"]) / "llm_comparison.json"
        comparison = json.loads(comparison_path.read_text(encoding="utf-8")) if comparison_path.is_file() else None
        return {
            "record": record,
            "capsule_path": path,
            "comparison": self._compact_comparison(comparison),
        }

    def capsule_payload(self, capsule_id: str) -> dict[str, Any] | None:
        payload = self.capsule_artifact_payload(capsule_id)
        if not payload:
            return None
        return {
            "record": payload["record"],
            "capsule": json.loads(payload["capsule_path"].read_text(encoding="utf-8")),
            "comparison": payload["comparison"],
        }

    def incident_report_payload(self, incident_id: str) -> dict[str, Any] | None:
        """Return the responder-facing report for an incident when a capsule exists."""

        incident = self.store.get_incident(incident_id)
        if not incident:
            return None
        capsule_record = self.store.get_capsule_for_incident(incident_id)
        if not capsule_record:
            return {"incident": incident, "report": None}
        capsule_path = Path(capsule_record["output_dir"]) / "capsule.json"
        if not capsule_path.is_file():
            return {"incident": incident, "report": None}
        report_path = Path(capsule_record["output_dir"]) / "incident_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
        if not report or report.get("report_version") != "1.3":
            # Retained reports must remain readable after source telemetry expires.
            capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
            try:
                source_metrics = (
                    _validate_metrics(read_case_json(Path(incident["case_dir"]) / "prometheus_metrics.json"))
                    if Path(incident["case_dir"]).is_dir()
                    else None
                )
            except FileNotFoundError:
                source_metrics = None
            report = build_incident_report(capsule, incident, source_metrics)
            write_json(report_path, report)
            create_archive(Path(capsule_record["output_dir"]), incident_id)
        briefing_path = Path(capsule_record["output_dir"]) / "ai_briefing.json"
        archive_path = Path(capsule_record["archive_path"])
        retention_days = self.general_configuration()["incident_retention_days"]
        expires_at = datetime.fromisoformat(incident["created_at"].replace("Z", "+00:00")) + timedelta(days=retention_days)
        episode = self.store.episode_for_incident(incident_id)
        episode_id = str(episode["episode_id"]) if episode else None
        return {
            "incident": incident,
            "record": capsule_record,
            "report": report,
            "storage": {
                "directory": str(capsule_path.parent),
                "archive_bytes": archive_path.stat().st_size if archive_path.is_file() else None,
                "report_bytes": report_path.stat().st_size,
                "retention_days": retention_days,
                "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            },
            "ai_briefing": json.loads(briefing_path.read_text(encoding="utf-8")) if briefing_path.is_file() else None,
            "investigation": self.investigator.for_incident(incident_id),
            "media_evidence": self.evidence.list(episode_id) if episode_id else [],
            "investigation_revisions": self.investigator.revisions(episode_id) if episode_id else [],
            "source_disconnected_reviews": self.investigator.source_disconnected_reviews(episode_id) if episode_id else [],
        }

    def start_ai_briefing(self, incident_id: str, retry: bool = False) -> dict[str, Any]:
        """Persist loading state before dispatch; deduplicate concurrent requests."""
        with self.briefing_lock:
            record = self.store.get_capsule_for_incident(incident_id)
            if not record:
                raise ValueError("Build a report before requesting a briefing")
            path = Path(record["output_dir"]) / "ai_briefing.json"
            previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            if incident_id in self.briefing_jobs:
                return previous
            if not retry and (previous.get("status") in {"rejected", "unavailable"}
                              or (previous.get("status") == "ready" and previous.get("briefing_version") == "2")):
                return previous
            if not self.ai_configuration()["api_key_configured"]:
                result = {"status": "not_configured", "message": "Add a provider key in Settings to enable automatic analysis."}
                self._write_briefing_state(path, result)
                return result
            result = {"status": "queued", "message": "Waiting for analysis", "queued_at": utc_now()}
            self._write_briefing_state(path, result)
            self.briefing_jobs.add(incident_id)
            self.briefing_executor.submit(self._run_ai_briefing, incident_id)
            return result

    @staticmethod
    def _write_briefing_state(path: Path, result: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        write_json(temporary, result)
        temporary.replace(path)

    def resume_ai_briefings(self) -> None:
        """Resume interrupted work and brief retained, unarchived episode signals."""
        for episode in self.store.list_episodes(limit=10000):
            for signal in episode["signals"]:
                if signal.get("report_ready"):
                    self.start_ai_briefing(str(signal["incident_id"]))

    def _run_ai_briefing(self, incident_id: str) -> None:
        try:
            with self.briefing_lock:
                record = self.store.get_capsule_for_incident(incident_id)
                if not record:
                    return
                path = Path(record["output_dir"]) / "ai_briefing.json"
                self._write_briefing_state(path, {"status": "running", "message": "Analysing retained evidence", "started_at": utc_now()})
            self.generate_ai_briefing(incident_id)
        except Exception:
            with self.briefing_lock:
                record = self.store.get_capsule_for_incident(incident_id)
                if record:
                    self._write_briefing_state(Path(record["output_dir"]) / "ai_briefing.json", {
                        "status": "unavailable", "message": "Analysis could not complete. Retained evidence is still available. Retry when the provider is reachable."
                    })
        finally:
            with self.briefing_lock:
                self.briefing_jobs.discard(incident_id)

    def generate_ai_briefing(self, incident_id: str) -> dict[str, Any]:
        payload = self.incident_report_payload(incident_id)
        if not payload or not payload.get("report") or not payload.get("record"):
            raise ValueError("Build an incident report before requesting an AI briefing")
        config = self.ai_configuration()
        result = generate_incident_briefing(
            payload["report"], model=config["model"], max_tokens=config["max_tokens"],
            provider=config["provider"],
        )
        with self.briefing_lock:
            if self.store.get_incident(incident_id):
                output_dir = Path(payload["record"]["output_dir"])
                self._write_briefing_state(output_dir / "ai_briefing.json", result)
                if result.get("status") == "ready":
                    create_archive(output_dir, incident_id)
        return result

    @staticmethod
    def _compact_comparison(comparison: dict[str, Any] | None) -> dict[str, Any] | None:
        if not comparison:
            return None
        return {
            "winner": comparison.get("winner"),
            "score_delta": comparison.get("score_delta"),
            "interpretation": comparison.get("interpretation"),
            "results": [
                {
                    "model": item.get("model"),
                    "status": item.get("status"),
                    "latency_seconds": item.get("latency_seconds"),
                    "total_tokens": item.get("usage", {}).get("total_tokens"),
                    "total_score": item.get("score", {}).get("total_score"),
                    "signal_score": item.get("score", {}).get("expected_signal_score"),
                    "signal_depth_score": item.get("score", {}).get("signal_depth_score"),
                    "citation_score": item.get("score", {}).get("citation_score"),
                    "domain_score": item.get("score", {}).get("domain_score"),
                }
                for item in comparison.get("results", [])
            ],
        }
