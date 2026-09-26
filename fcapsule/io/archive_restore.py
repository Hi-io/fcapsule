"""Restore verified capsule files without importing application state.

This module only materializes archive files. It does not register capsules or
incidents, run investigations, or publish records to Collective.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from zipfile import ZipFile

from fcapsule.io.archive_writer import MANIFEST_NAME, verify_archive


MAX_RESTORED_BYTES = 512 * 1024 * 1024


def restore_archive_files(
    archive_path: str | Path,
    destination_dir: str | Path,
    *,
    max_bytes: int = MAX_RESTORED_BYTES,
) -> Path:
    """Extract a verified archive into a new directory without changing references."""
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")

    destination = Path(destination_dir).resolve()
    if destination.exists():
        raise FileExistsError(f"Restore destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Restore parent directory does not exist: {destination.parent}")

    manifest = verify_archive(Path(archive_path))
    if sum(item["size_bytes"] for item in manifest["files"]) > max_bytes:
        raise ValueError(f"Capsule archive exceeds the restore limit of {max_bytes} bytes")

    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent))
    try:
        with ZipFile(archive_path) as archive:
            if json.loads(archive.read(MANIFEST_NAME)) != manifest:
                raise ValueError("Capsule archive changed during restore")
            for item in manifest["files"]:
                name = item["path"]
                size = 0
                digest = hashlib.sha256()
                with archive.open(name) as source, (staging / name).open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > item["size_bytes"]:
                            raise ValueError(f"Capsule archive size check failed for {name}")
                        target.write(chunk)
                        digest.update(chunk)
                if size != item["size_bytes"] or digest.hexdigest() != item["sha256"]:
                    raise ValueError(f"Capsule archive integrity check failed for {name}")
            (staging / MANIFEST_NAME).write_bytes(archive.read(MANIFEST_NAME))
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return destination
