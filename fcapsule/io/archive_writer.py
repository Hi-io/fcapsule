"""Create a portable capsule archive without copying raw telemetry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile


MANIFEST_NAME = "capsule_manifest.json"
ARCHIVE_FORMAT = "fcapsule.capsule_archive"
ARCHIVE_FORMAT_VERSION = 1
ARCHIVE_FILES = (
    "capsule.md",
    "capsule.json",
    "evidence.json",
    "evaluation.json",
    "baselines.json",
    "incident_report.json",
    "ai_briefing.json",
    "episode_investigation.json",
    "investigation_history.json",
    "investigation_revisions.json",
    "evidence_manifest.json",
    "dashboard.html",
)


def create_archive(output_dir: Path, case_id: str) -> Path:
    archive_path = output_dir / f"fcapsule_{case_id}.zip"
    with NamedTemporaryFile(dir=output_dir, suffix=".zip.tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
            files = []
            for name in ARCHIVE_FILES:
                path = output_dir / name
                if path.exists():
                    digest = hashlib.sha256()
                    size = 0
                    with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as entry:
                        while chunk := source.read(1024 * 1024):
                            entry.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    files.append({
                        "path": name,
                        "size_bytes": size,
                        "sha256": digest.hexdigest(),
                    })
            manifest = {
                "format": ARCHIVE_FORMAT,
                "format_version": ARCHIVE_FORMAT_VERSION,
                "files": files,
            }
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        temporary_path.replace(archive_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return archive_path


def verify_archive(archive_path: Path) -> dict:
    """Validate a capsule archive's supported format and every declared file."""
    try:
        with ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("Capsule archive contains duplicate paths")
            if MANIFEST_NAME not in names:
                raise ValueError("Capsule archive has no integrity manifest")
            manifest_info = archive.getinfo(MANIFEST_NAME)
            if manifest_info.file_size > 1_048_576:
                raise ValueError("Capsule archive manifest is too large")
            try:
                manifest = json.loads(archive.read(MANIFEST_NAME))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Capsule archive manifest is invalid JSON") from exc
            if not isinstance(manifest, dict) or manifest.get("format") != ARCHIVE_FORMAT:
                raise ValueError("Unsupported capsule archive format")
            version = manifest.get("format_version")
            if type(version) is not int or version != ARCHIVE_FORMAT_VERSION:
                raise ValueError("Unsupported capsule archive version")
            files = manifest.get("files")
            if not isinstance(files, list):
                raise ValueError("Capsule archive manifest has no file list")

            declared = {}
            for item in files:
                if not isinstance(item, dict):
                    raise ValueError("Capsule archive manifest contains an invalid file entry")
                name, size, digest = item.get("path"), item.get("size_bytes"), item.get("sha256")
                if name not in ARCHIVE_FILES or name in declared:
                    raise ValueError("Capsule archive manifest contains an invalid or duplicate path")
                if type(size) is not int or size < 0 or not isinstance(digest, str) or len(digest) != 64:
                    raise ValueError("Capsule archive manifest contains invalid integrity metadata")
                if any(character not in "0123456789abcdef" for character in digest):
                    raise ValueError("Capsule archive manifest contains an invalid SHA-256 digest")
                declared[name] = (size, digest)

            if set(names) != set(declared) | {MANIFEST_NAME}:
                raise ValueError("Capsule archive has missing or undeclared files")
            for name, (expected_size, expected_digest) in declared.items():
                info = archive.getinfo(name)
                if info.file_size != expected_size:
                    raise ValueError(f"Capsule archive size check failed for {name}")
                digest = hashlib.sha256()
                size = 0
                with archive.open(name) as content:
                    while chunk := content.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                if size != expected_size or digest.hexdigest() != expected_digest:
                    raise ValueError(f"Capsule archive integrity check failed for {name}")
            return manifest
    except BadZipFile as exc:
        raise ValueError("Capsule archive is not a valid ZIP file") from exc
