import hashlib
import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from fcapsule.estima_publisher import EstimaPublisher
from fcapsule.io.archive_restore import restore_archive_files
from fcapsule.pipeline import investigate_case
from fcapsule.store import FCAPSuleStore
from tests.common import REFERENCE_CASE


class ArchiveRestoreTests(unittest.TestCase):
    def _make_archive(self, root: Path) -> Path:
        output = root / "output"
        investigate_case(REFERENCE_CASE, output)
        return output / "fcapsule_case_001.zip"

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
