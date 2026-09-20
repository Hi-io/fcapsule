"""Create a portable capsule archive without copying raw telemetry."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
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
        "ai_briefing.json",
        "episode_investigation.json",
        "dashboard.html",
    )
    with NamedTemporaryFile(dir=output_dir, suffix=".zip.tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
            for name in names:
                path = output_dir / name
                if path.exists():
                    archive.write(path, arcname=name)
        temporary_path.replace(archive_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return archive_path
