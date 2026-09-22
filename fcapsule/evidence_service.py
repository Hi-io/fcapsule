"""Bounded, optional media evidence for an existing FCAPSule episode.

Attachments are deliberately separate from the automatic capture path. They are
stored locally, extracted asynchronously through an already-validated specialist,
and only affect an investigation after the operator explicitly requests a revision.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fcapsule.processing.anonymizer import anonymize_text
from fcapsule.reasoning.openrouter import OpenRouterClient, OpenRouterError


IMAGE_LIMIT = 6 * 1024 * 1024
AUDIO_LIMIT = 8 * 1024 * 1024
_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_text(value: Any, limit: int) -> str:
    return anonymize_text(str(value or ""))[:limit].strip()


def _parse_observed_at(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Observed time must be an ISO-8601 timestamp") from error
    return text


def _media_type(data: bytes, kind: str) -> tuple[str, str]:
    if kind == "image":
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", ".png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", ".jpg"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp", ".webp"
        raise ValueError("Image evidence must be a PNG, JPEG, or WebP file")
    if kind == "audio":
        if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
            return "audio/wav", ".wav"
        if data.startswith(b"ID3") or data[:2] == b"\xff\xfb":
            return "audio/mpeg", ".mp3"
        if data.startswith(b"OggS"):
            return "audio/ogg", ".ogg"
        if data.startswith(b"\x1aE\xdf\xa3"):
            return "audio/webm", ".webm"
        if len(data) >= 12 and data[4:8] == b"ftyp":
            return "audio/mp4", ".m4a"
        raise ValueError("Audio evidence must be WAV, MP3, OGG, WebM, or M4A")
    raise ValueError("Evidence kind must be image or audio")


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    parsed: Any = None
    candidates = [text]
    candidates.extend(text[index:] for index, character in enumerate(text) if character == "{")
    for candidate in candidates:
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            parsed = value
            break
    if parsed is None:
        return {"visible_text": [], "observations": [], "ambiguities": ["The visual model did not return structured extraction."],
                "limitation": "Treat this attachment as available but not machine-extracted."}
    visible_text = [_safe_text(item, 240) for item in parsed.get("visible_text", []) if str(item).strip()][:20]
    observations = []
    for item in parsed.get("observations", [])[:16]:
        if not isinstance(item, dict):
            continue
        fact = _safe_text(item.get("fact"), 420)
        if not fact:
            continue
        supplied_confidence = item.get("confidence")
        if isinstance(supplied_confidence, (int, float)) and not isinstance(supplied_confidence, bool):
            confidence = "high" if supplied_confidence >= 0.8 else "medium" if supplied_confidence >= 0.5 else "low"
        else:
            confidence = str(supplied_confidence or "unspecified").lower()
        if confidence not in {"high", "medium", "low", "unspecified"}:
            confidence = "unspecified"
        observations.append({"fact": fact, "confidence": confidence, "region": _safe_text(item.get("region"), 120)})
    ambiguities = [_safe_text(item, 220) for item in parsed.get("ambiguities", []) if str(item).strip()][:12]
    return {
        "visible_text": visible_text,
        "observations": observations,
        "ambiguities": ambiguities,
        "limitation": _safe_text(parsed.get("limitation"), 420) or "Visible content is not independently verified.",
    }


class EvidenceService:
    def __init__(self, plane: Any) -> None:
        self.plane = plane
        self.root = plane.state_dir / "evidence"
        self.root.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fcapsule-evidence")

    def shutdown(self, wait: bool = True) -> None:
        """Stop media work; callers that need immediate process shutdown may opt out of waiting."""

        self.executor.shutdown(wait=wait, cancel_futures=True)

    def _public(self, record: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in record.items() if key != "storage_path"}
        result["artifact_url"] = f"/api/evidence/{record['attachment_id']}/file"
        return result

    def list(self, episode_id: str) -> list[dict[str, Any]]:
        return [self._public(item) for item in self.plane.store.list_evidence_attachments(episode_id)]

    def _write(self, attachment_id: str, extension: str, data: bytes) -> Path:
        episode_key = hashlib.sha256(attachment_id.encode("utf-8")).hexdigest()[:16]
        directory = self.root / episode_key
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{attachment_id}{extension}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    def submit(self, episode_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.plane.store.get_episode(episode_id):
            raise KeyError("Episode not found")
        kind = str(payload.get("kind") or "").strip().lower()
        capability = "vision" if kind == "image" else "audio" if kind == "audio" else ""
        if not capability:
            raise ValueError("Evidence kind must be image or audio")
        allowed, reason = self.plane.media_submission_allowed(capability)
        if not allowed:
            raise ValueError(reason)
        encoded = str(payload.get("content_base64") or "")
        if not encoded or len(encoded) > ((IMAGE_LIMIT if kind == "image" else AUDIO_LIMIT) * 2):
            raise ValueError("Evidence payload is missing or exceeds the upload boundary")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("Evidence payload is not valid base64") from error
        limit = IMAGE_LIMIT if kind == "image" else AUDIO_LIMIT
        if not data or len(data) > limit:
            raise ValueError("Evidence file exceeds the permitted upload size")
        mime_type, extension = _media_type(data, kind)
        name = _FILENAME.sub("-", Path(str(payload.get("filename") or f"evidence{extension}")).name).strip(".-")[:96]
        filename = (name or f"evidence{extension}")
        if not filename.lower().endswith(extension):
            filename += extension
        attachment_id = f"attachment-{uuid.uuid4().hex}"
        storage_path = self._write(attachment_id, extension, data)
        config = self.plane.media_configuration()
        specialist = config[capability]
        record = self.plane.store.record_evidence_attachment(
            {
                "attachment_id": attachment_id,
                "episode_id": episode_id,
                "kind": kind,
                "filename": filename,
                "mime_type": mime_type,
                "storage_path": storage_path,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "observed_at": _parse_observed_at(payload.get("observed_at")),
                "context_note": _safe_text(payload.get("context_note"), 1000),
                "source_redacted": bool(payload.get("source_redacted")),
                "status": "queued",
                "provider": "openrouter",
                "model": specialist["model"],
            }
        )
        self.executor.submit(self._process, attachment_id)
        return self._public(record)

    def _process(self, attachment_id: str) -> None:
        attachment = self.plane.store.get_evidence_attachment(attachment_id)
        if not attachment:
            return
        capability = "vision" if attachment["kind"] == "image" else "audio"
        allowed, reason = self.plane.media_submission_allowed(capability)
        if not allowed:
            self.plane.store.update_evidence_attachment(attachment_id, status="blocked", extraction={"limitation": reason})
            self.plane.refresh_evidence_exports(str(attachment["episode_id"]))
            return
        try:
            data = Path(attachment["storage_path"]).read_bytes()
            config = self.plane.media_configuration()[capability]
            client = OpenRouterClient(timeout_seconds=90)
            if attachment["kind"] == "image":
                response = client.visual_extract(data, attachment["mime_type"], str(config["model"]))
                extraction = _extract_json(str(response["content"]))
            else:
                response = client.transcribe(data, attachment["mime_type"], str(config["model"]))
                extraction = {
                    "transcript": _safe_text(response.get("transcript"), 4000),
                    "segments": [
                        {"start": item.get("start"), "end": item.get("end"), "text": _safe_text(item.get("text"), 360)}
                        for item in response.get("segments", [])[:40] if isinstance(item, dict)
                    ],
                    "limitation": "Transcript is operator-provided evidence and may describe an earlier event.",
                }
            self.plane.store.update_evidence_attachment(
                attachment_id,
                status="ready",
                extraction=extraction,
                provider="openrouter",
                model=str(config["model"]),
                usage=response.get("usage") if isinstance(response.get("usage"), dict) else {},
            )
        except (OSError, OpenRouterError, ValueError) as error:
            self.plane.store.update_evidence_attachment(
                attachment_id,
                status="failed",
                extraction={"limitation": _safe_text(error, 420)},
            )
        finally:
            refreshed = self.plane.store.get_evidence_attachment(attachment_id)
            if refreshed:
                self.plane.refresh_evidence_exports(str(refreshed["episode_id"]))

    def correct(self, attachment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        correction = _safe_text(payload.get("correction"), 2000)
        observed_at = _parse_observed_at(payload.get("observed_at")) if "observed_at" in payload else None
        context_note = _safe_text(payload.get("context_note"), 1000) if "context_note" in payload else None
        record = self.plane.store.update_evidence_attachment(
            attachment_id, correction=correction, observed_at=observed_at, context_note=context_note
        )
        return self._public(record)

    def remove(self, attachment_id: str) -> None:
        record = self.plane.store.delete_evidence_attachment(attachment_id)
        path = Path(record["storage_path"])
        try:
            path.relative_to(self.root)
        except ValueError:
            return
        path.unlink(missing_ok=True)

    def file(self, attachment_id: str) -> tuple[Path, str] | None:
        record = self.plane.store.get_evidence_attachment(attachment_id)
        if not record:
            return None
        path = Path(record["storage_path"]).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            return None
        return (path, str(record["mime_type"])) if path.is_file() else None

    def model_evidence(self, episode_id: str) -> list[dict[str, Any]]:
        evidence = []
        for attachment in self.plane.store.list_evidence_attachments(episode_id):
            if attachment["status"] != "ready":
                continue
            extraction = attachment.get("extraction") or {}
            correction = _safe_text(attachment.get("correction"), 1200)
            if attachment["kind"] == "image":
                observations = extraction.get("observations") if isinstance(extraction.get("observations"), list) else []
                facts = [str(item.get("fact")) for item in observations if isinstance(item, dict) and item.get("fact")]
                text = extraction.get("visible_text") if isinstance(extraction.get("visible_text"), list) else []
                summary = "; ".join([*facts[:8], *[str(item) for item in text[:6]]]) or "Image evidence was accepted without a readable extracted observation."
                domain = "image_evidence"
                examples = observations[:4]
            else:
                summary = str(extraction.get("transcript") or "") or "Audio evidence was accepted without a transcript."
                domain = "audio_transcript"
                examples = extraction.get("segments", [])[:4] if isinstance(extraction.get("segments"), list) else []
            evidence.append(
                {
                    "id": "A-" + str(attachment["attachment_id"]),
                    "domain": domain,
                    "title": f"{attachment['kind'].title()} evidence: {attachment['filename']}",
                    "summary": _safe_text(summary, 1800),
                    "time_range": {"observed_at": attachment.get("observed_at"), "uploaded_at": attachment.get("created_at")},
                    "examples": examples,
                    "attachment_id": attachment["attachment_id"],
                    "operator_context": {
                        key: value for key, value in {
                            "note": _safe_text(attachment.get("context_note"), 800),
                            "correction": correction,
                        }.items() if value
                    },
                    "limitation": extraction.get("limitation") or "Externally supplied evidence is not independent verification.",
                }
            )
        return evidence

    def manifest(self, episode_id: str) -> list[dict[str, Any]]:
        records = []
        for item in self.plane.store.list_evidence_attachments(episode_id):
            records.append(
                {
                    "attachment_id": item["attachment_id"], "kind": item["kind"], "filename": item["filename"],
                    "mime_type": item["mime_type"], "size_bytes": item["size_bytes"], "sha256": item["sha256"],
                    "status": item["status"], "observed_at": item.get("observed_at"), "uploaded_at": item["created_at"],
                    "provider": item.get("provider"), "model": item.get("model"), "usage": item.get("usage", {}),
                    "source_redacted": item.get("source_redacted", False),
                    "context_note": _safe_text(item.get("context_note"), 1000),
                    "correction": _safe_text(item.get("correction"), 2000),
                    "extraction": item.get("extraction") if isinstance(item.get("extraction"), dict) else {},
                }
            )
        return records
