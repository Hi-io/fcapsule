"""Explicitly repack trusted legacy capsule files into the manifested format."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from fcapsule.io.archive_writer import ARCHIVE_FILES, MANIFEST_NAME, create_archive, verify_archive


MAX_LEGACY_ARCHIVE_BYTES = 512 * 1024 * 1024
LEGACY_REPACK_WARNING = (
    "Unverified origin accepted: new hashes start at repack time and cannot attest the original archive's integrity or provenance."
)
_REQUIRED_IMPORT_FILES = {"capsule.json", "evidence.json", "incident_report.json"}
_OMIT_ON_REPACK = {"dashboard.html"}
_JSON_FILE_LIMITS = {"capsule.json": 8 * 1024 * 1024, "incident_report.json": 2 * 1024 * 1024}


def _validate_import_files(directory: Path) -> None:
    missing = [name for name in sorted(_REQUIRED_IMPORT_FILES) if not (directory / name).is_file()]
    if missing:
        raise ValueError("Legacy archive is missing import files: " + ", ".join(missing))
    for name, limit in _JSON_FILE_LIMITS.items():
        if (directory / name).stat().st_size > limit:
            raise ValueError(f"Legacy archive {name} exceeds the validation limit of {limit} bytes")
    try:
        capsule = json.loads((directory / "capsule.json").read_text(encoding="utf-8"))
        report = json.loads((directory / "incident_report.json").read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Legacy archive has invalid capsule or incident report JSON") from exc
    if not isinstance(capsule, dict) or not isinstance(report, dict):
        raise ValueError("Legacy capsule and incident report must be JSON objects")
    if report.get("report_version") != "1.3":
        raise ValueError("Legacy archive requires a version 1.3 incident report for import")
    incident = report.get("incident")
    source_id = incident.get("incident_id") if isinstance(incident, dict) else None
    if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", source_id):
        raise ValueError("Legacy archive has an unsupported source incident ID")
    if not isinstance(capsule.get("case"), dict) or not isinstance(capsule.get("selected_evidence"), list):
        raise ValueError("Legacy capsule is missing retained case or evidence references")


def repack_legacy_archive(
    archive_path: str | Path,
    output_path: str | Path,
    *,
    accept_unverified_origin: bool = False,
    max_bytes: int = MAX_LEGACY_ARCHIVE_BYTES,
) -> Path:
    """Repack allowlisted legacy files without claiming their original provenance."""
    if accept_unverified_origin is not True:
        raise ValueError("Repacking requires explicit --accept-unverified-origin acknowledgement")
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")

    source = Path(archive_path).resolve()
    output = Path(output_path).resolve()
    if source == output:
        raise ValueError("Output must be a new path; the original archive is never modified")
    if not source.is_file():
        raise FileNotFoundError(f"Legacy capsule archive not found: {source}")
    if source.stat().st_size > max_bytes:
        raise ValueError(f"Legacy capsule archive exceeds the repack limit of {max_bytes} bytes")
    if not output.parent.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {output.parent}")
    if output.exists():
        raise FileExistsError(f"Output archive already exists: {output}")

    try:
        with ZipFile(source) as legacy:
            infos = legacy.infolist()
            names = [getattr(info, "orig_filename", info.filename) for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("Legacy archive contains duplicate paths")
            if MANIFEST_NAME in names:
                raise ValueError("Archive already contains a manifest; use import-archive")
            if not infos:
                raise ValueError("Legacy archive is empty")

            allowed = set(ARCHIVE_FILES)
            total_declared = 0
            for info, name in zip(infos, names):
                mode = (info.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG):
                    raise ValueError(f"Legacy archive contains a non-regular file: {name}")
                if info.is_dir() or name not in allowed or info.filename != name or "/" in name or "\\" in name:
                    raise ValueError(f"Legacy archive contains an unsupported path: {name}")
                if info.flag_bits & 0x1:
                    raise ValueError(f"Legacy archive contains an encrypted file: {name}")
                if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                    raise ValueError(f"Legacy archive uses an unsupported compression method: {name}")
                if type(info.file_size) is not int or info.file_size < 0:
                    raise ValueError(f"Legacy archive has an invalid size for {name}")
                total_declared += info.file_size
                if total_declared > max_bytes:
                    raise ValueError(f"Legacy archive exceeds the repack limit of {max_bytes} bytes")

            with tempfile.TemporaryDirectory(prefix=".fcapsule-legacy-repack-", dir=output.parent) as temporary:
                staging = Path(temporary)
                total_written = 0
                for info, name in zip(infos, names):
                    if name in _OMIT_ON_REPACK:
                        continue
                    target = staging / name
                    size = 0
                    with legacy.open(info) as content, target.open("xb") as restored:
                        while chunk := content.read(1024 * 1024):
                            size += len(chunk)
                            total_written += len(chunk)
                            if size > info.file_size or total_written > max_bytes:
                                raise ValueError(f"Legacy archive exceeds its declared size or repack limit: {name}")
                            restored.write(chunk)
                    if size != info.file_size:
                        raise ValueError(f"Legacy archive size check failed for {name}")

                _validate_import_files(staging)
                source_id = json.loads((staging / "incident_report.json").read_text(encoding="utf-8"))["incident"]["incident_id"]
                repacked = create_archive(staging, source_id)
                verify_archive(repacked)

                created_output = False
                try:
                    with output.open("xb") as destination:
                        created_output = True
                        with repacked.open("rb") as verified:
                            shutil.copyfileobj(verified, destination, length=1024 * 1024)
                        destination.flush()
                        os.fsync(destination.fileno())
                except Exception:
                    if created_output:
                        output.unlink(missing_ok=True)
                    raise
    except (BadZipFile, EOFError, zlib.error) as exc:
        raise ValueError("Legacy capsule archive is not a valid ZIP file") from exc
    return output
