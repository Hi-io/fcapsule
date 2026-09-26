import hashlib
import json
import shutil
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from fcapsule.archive_import import import_capsule_archive
from fcapsule.control_plane import ControlPlane
from fcapsule.estima_publisher import EstimaPublisher
from fcapsule.incident_report import build_incident_report
from fcapsule.io.archive_writer import create_archive
from fcapsule.io.archive_restore import restore_archive_files
from fcapsule.io.output_writer import write_json
from fcapsule.pipeline import investigate_case
from fcapsule.store import FCAPSuleStore
from tests.common import REFERENCE_CASE


class ArchiveRestoreTests(unittest.TestCase):
    def _make_archive(self, root: Path) -> Path:
        output = root / "output"
        investigate_case(REFERENCE_CASE, output)
        capsule = json.loads((output / "capsule.json").read_text(encoding="utf-8"))
        started_at = capsule["case"]["window"]["start"]
        ended_at = capsule["case"]["window"]["end"]
        incident = {
            "incident_id": "source-capture-01",
            "app_id": "source-app",
            "status": "resolved",
            "severity": "warning",
            "started_at": started_at,
            "ended_at": ended_at,
            "summary": "Retained source incident",
        }
        write_json(output / "incident_report.json", build_incident_report(capsule, incident))
        return create_archive(output, incident["incident_id"])

    def test_restores_verified_files_without_original_output_or_reference_rewrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = self._make_archive(root)
            downloaded_archive = root / "downloaded.zip"
            archive_path.replace(downloaded_archive)
            shutil.rmtree(root / "output")

            destination = restore_archive_files(downloaded_archive, root / "restored")

            with ZipFile(downloaded_archive) as archive:
                for name in archive.namelist():
                    self.assertEqual((destination / name).read_bytes(), archive.read(name))
                capsule = json.loads(archive.read("capsule.json"))
                self.assertEqual(json.loads((destination / "capsule.json").read_text(encoding="utf-8")), capsule)

    def test_restore_does_not_touch_store_or_collective_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = self._make_archive(root)
            methods = (
                (FCAPSuleStore, "record_incident"),
                (FCAPSuleStore, "record_capsule"),
                (FCAPSuleStore, "enqueue_atlas_publication"),
                (EstimaPublisher, "notify_episode"),
                (EstimaPublisher, "scan_completed"),
            )
            with ExitStack() as stack:
                spies = [stack.enter_context(patch.object(target, name)) for target, name in methods]
                restore_archive_files(archive_path, root / "restored")
            for spy in spies:
                spy.assert_not_called()

    def test_restore_rejects_existing_destination_without_changing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = self._make_archive(root)
            destination = root / "restored"
            destination.mkdir()
            sentinel = destination / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                restore_archive_files(archive_path, destination)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_restore_enforces_expanded_size_limit_before_creating_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = self._make_archive(root)
            destination = root / "restored"

            with self.assertRaisesRegex(ValueError, "restore limit"):
                restore_archive_files(archive_path, destination, max_bytes=1)
            self.assertFalse(destination.exists())

    def test_import_registers_local_retained_record_after_source_is_gone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self._make_archive(root)
            downloaded = root / "downloaded.zip"
            archive.replace(downloaded)
            shutil.rmtree(root / "output")

            with ExitStack() as stack:
                spies = [
                    stack.enter_context(patch.object(FCAPSuleStore, name))
                    for name in ("record_incident", "record_capsule", "enqueue_atlas_publication")
                ]
                spies.extend(
                    stack.enter_context(patch.object(EstimaPublisher, name))
                    for name in ("notify_episode", "scan_completed")
                )
                imported = import_capsule_archive(downloaded, root / "state")
            for spy in spies:
                spy.assert_not_called()

            incident = imported["incident"]
            capsule_record = imported["capsule"]
            self.assertEqual(incident["source_kind"], "imported")
            self.assertEqual(incident["status"], "resolved")
            self.assertGreater(incident["created_at"], incident["started_at"])
            self.assertEqual(imported["application"]["status"], "unknown")
            self.assertFalse(Path(incident["case_dir"]).exists())
            self.assertTrue(Path(capsule_record["archive_path"]).is_file())
            self.assertEqual(
                Path(capsule_record["archive_path"]).name,
                "fcapsule_source-capture-01.zip",
            )

            with ZipFile(downloaded) as source:
                original_report = json.loads(source.read("incident_report.json"))
                original_capsule = json.loads(source.read("capsule.json"))
            downloaded.unlink()
            plane = ControlPlane(root / "state")
            payload = plane.incident_report_payload(incident["incident_id"])
            self.assertEqual(payload["report"], original_report)
            self.assertEqual(payload["report"]["supporting_evidence"], original_capsule["selected_evidence"])
            retained = plane.capsule_payload(capsule_record["capsule_id"])
            self.assertEqual(retained["capsule"]["selected_evidence"], original_capsule["selected_evidence"])
            with self.assertRaisesRegex(ValueError, "read-only"):
                plane._build_capsule(incident["incident_id"])

            with patch.object(plane, "_effective_estima_settings", return_value={"publish_enabled": True, "url": "https://collective.invalid"}), \
                    patch.object(plane.investigator, "read", side_effect=AssertionError("import must not start investigation")) as read, \
                    patch.object(plane.store, "enqueue_atlas_publication") as enqueue:
                self.assertEqual(plane.estima_publisher.scan_completed(), 0)
                read.assert_not_called()
                enqueue.assert_not_called()

            report_path = Path(capsule_record["output_dir"]) / "incident_report.json"
            report_path.unlink()
            with patch("fcapsule.control_plane.investigate_case", side_effect=AssertionError("import must not rebuild from source")):
                missing_report = plane.incident_report_payload(incident["incident_id"])
            self.assertIsNone(missing_report["report"])

    def test_import_id_collision_fails_without_overwriting_existing_application(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self._make_archive(root)
            state = root / "state"
            token = "a" * 32
            store = FCAPSuleStore(state / "fcapsule.db")
            existing = store.upsert_application(f"import-app-{token}", "Existing", "ns", "cluster")

            with patch("fcapsule.archive_import.uuid.uuid4", return_value=SimpleNamespace(hex=token)):
                with self.assertRaisesRegex(ValueError, "ID collision"):
                    import_capsule_archive(archive, state)

            self.assertEqual(store.get_application(existing["app_id"])["name"], "Existing")
            self.assertEqual(store.list_incidents(), [])
            self.assertFalse((state / "imported-capsules" / token).exists())

    def test_restore_rejects_untrusted_archive_paths_without_writing_outside(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "untrusted.zip"
            content = b"outside"
            manifest = {
                "format": "fcapsule.capsule_archive",
                "format_version": 1,
                "files": [{
                    "path": "../escaped.txt",
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }],
            }
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("../escaped.txt", content)
                archive.writestr("capsule_manifest.json", json.dumps(manifest))

            destination = root / "restored"
            with self.assertRaisesRegex(ValueError, "invalid or duplicate path"):
                restore_archive_files(archive_path, destination)
            self.assertFalse(destination.exists())
            self.assertFalse((root / "escaped.txt").exists())


if __name__ == "__main__":
    unittest.main()
