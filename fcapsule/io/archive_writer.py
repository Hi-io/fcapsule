"""Create a portable capsule archive without copying raw telemetry."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def create_archive(output_dir: Path, case_id: str) -> Path:
    archive_path = output_dir / f"fcapsule_{case_id}.zip"
    names = (
        "capsule.md",
        "capsule.json",
        "evidence.json",
        "evaluation.json",
        "baselines.json",
        "incident_report.json",
        "dashboard.html",
    )
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for name in names:
            path = output_dir / name
            if path.exists():
                archive.write(path, arcname=name)
    return archive_path
