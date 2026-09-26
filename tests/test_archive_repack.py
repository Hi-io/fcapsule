import hashlib
import json
import stat
import tempfile
import unittest
import warnings
from contextlib import ExitStack
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from unittest.mock import patch

from fcapsule.archive_import import import_capsule_archive
from fcapsule.estima_publisher import EstimaPublisher
from fcapsule.incident_report import build_incident_report
from fcapsule.io.archive_repack import LEGACY_REPACK_WARNING, repack_legacy_archive
from fcapsule.io.archive_writer import MANIFEST_NAME, create_archive, verify_archive
from fcapsule.io.output_writer import write_json
from fcapsule.pipeline import investigate_case
from fcapsule.store import FCAPSuleStore
from tests.common import REFERENCE_CASE


class LegacyArchiveRepackTests(unittest.TestCase):
    def _legacy_archive(self, root: Path) -> Path:
        output = root / "source-output"
        investigate_case(REFERENCE_CASE, output)
        capsule = json.loads((output / "capsule.json").read_text(encoding="utf-8"))
        incident = {
            "incident_id": "legacy-capture-01",
            "app_id": "source-app",
            "status": "resolved",
            "severity": "warning",
            "started_at": capsule["case"]["window"]["start"],
            "ended_at": capsule["case"]["window"]["end"],
            "summary": "Retained source incident",
        }
        write_json(output / "incident_report.json", build_incident_report(capsule, incident))
        current = create_archive(output, incident["incident_id"])
        legacy = root / "legacy.zip"
        with ZipFile(current) as source, ZipFile(legacy, "w", compression=ZIP_DEFLATED) as target:
            for info in source.infolist():
                if info.filename != MANIFEST_NAME:
                    target.writestr(info, source.read(info.filename))
        return legacy

    def _malicious_archive(self, path: Path, entries: list[ZipInfo]) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
                for info in entries:
                    archive.writestr(info, b"unsafe")

    def test_requires_explicit_origin_acknowledgment_without_writing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = self._legacy_archive(root)
            output = root / "repacked.zip"

            with self.assertRaisesRegex(ValueError, "explicit --accept-unverified-origin"):
                repack_legacy_archive(legacy, output)

            self.assertFalse(output.exists())

    def test_repack_preserves_source_and_creates_manifest_for_strict_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = self._legacy_archive(root)
            legacy_hash = hashlib.sha256(legacy.read_bytes()).hexdigest()
            output = root / "repacked.zip"
            methods = (
                (FCAPSuleStore, "register_imported_capsule"),
                (FCAPSuleStore, "enqueue_atlas_publication"),
                (EstimaPublisher, "notify_episode"),
                (EstimaPublisher, "scan_completed"),
            )
            with ExitStack() as stack:
                spies = [stack.enter_context(patch.object(target, name)) for target, name in methods]
                result = repack_legacy_archive(legacy, output, accept_unverified_origin=True)
            for spy in spies:
                spy.assert_not_called()

            self.assertEqual(result, output.resolve())
            self.assertEqual(hashlib.sha256(legacy.read_bytes()).hexdigest(), legacy_hash)
            manifest = verify_archive(output)
            paths = {item["path"] for item in manifest["files"]}
            self.assertIn("capsule.json", paths)
            self.assertIn("evidence.json", paths)
            self.assertIn("incident_report.json", paths)
            self.assertNotIn("dashboard.html", paths)
            self.assertIn("cannot attest", LEGACY_REPACK_WARNING)

            imported = import_capsule_archive(output, root / "state")
            self.assertEqual(imported["incident"]["source_kind"], "imported")
            self.assertEqual(imported["incident"]["summary"], "Retained source incident")

    def test_rejects_duplicate_traversal_and_symlink_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (
                ([ZipInfo("capsule.md"), ZipInfo("capsule.md")], "duplicate paths"),
                ([ZipInfo("../capsule.json")], "unsupported path"),
                ([self._symlink_info("capsule.json")], "non-regular file"),
            )
            for index, (entries, message) in enumerate(cases):
                archive = root / f"malicious-{index}.zip"
                output = root / f"out-{index}.zip"
                self._malicious_archive(archive, entries)
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    repack_legacy_archive(archive, output, accept_unverified_origin=True)
                self.assertFalse(output.exists())

    def test_rejects_expansion_over_limit_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = self._legacy_archive(root)
            output = root / "repacked.zip"
            with ZipFile(legacy) as archive:
                expanded_bytes = sum(info.file_size for info in archive.infolist())
            self.assertLess(legacy.stat().st_size, expanded_bytes)
            with self.assertRaisesRegex(ValueError, "repack limit"):
                repack_legacy_archive(
                    legacy, output, accept_unverified_origin=True, max_bytes=expanded_bytes - 1,
                )
            self.assertFalse(output.exists())

            output.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                repack_legacy_archive(legacy, output, accept_unverified_origin=True)
            self.assertEqual(output.read_bytes(), b"keep")

    def test_rejects_manifested_archive_and_same_input_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = self._legacy_archive(root)
            current = root / "current.zip"
            with ZipFile(legacy) as source, ZipFile(current, "w", compression=ZIP_DEFLATED) as target:
                for info in source.infolist():
                    target.writestr(info, source.read(info.filename))
                target.writestr(MANIFEST_NAME, "{}")
            with self.assertRaisesRegex(ValueError, "already contains a manifest"):
                repack_legacy_archive(current, root / "new.zip", accept_unverified_origin=True)
            with self.assertRaisesRegex(ValueError, "new path"):
                repack_legacy_archive(legacy, legacy, accept_unverified_origin=True)

    @staticmethod
    def _symlink_info(name: str) -> ZipInfo:
        info = ZipInfo(name)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        return info


if __name__ == "__main__":
    unittest.main()
