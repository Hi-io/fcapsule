import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fcapsule.io.case_loader import load_case
from fcapsule.models.schemas import CaseValidationError
from tests.common import REFERENCE_CASE


class CaseLoaderTests(unittest.TestCase):
    def test_loads_reference_case(self):
        bundle = load_case(REFERENCE_CASE)
        self.assertEqual(bundle.case_id, "case_001")
        self.assertEqual(len(bundle.alerts), 1)
        self.assertGreater(len(bundle.logs), 100)
        self.assertEqual(len(bundle.metrics), 7)

    def test_missing_required_file_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CaseValidationError, "Missing required case files"):
                load_case(directory)

    def test_invalid_timestamp_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "case"
            shutil.copytree(REFERENCE_CASE, target)
            alert_path = target / "alert.json"
            alert = json.loads(alert_path.read_text(encoding="utf-8"))
            alert["startsAt"] = "not-a-time"
            alert_path.write_text(json.dumps(alert), encoding="utf-8")
            with self.assertRaisesRegex(CaseValidationError, "invalid timestamp"):
                load_case(target)

    def test_case_id_must_be_a_bounded_filesystem_safe_identifier(self):
        invalid_ids = (
            "../outside", r"..\..\outside", "x" * 129, "", "case.", "CON", "NUL.txt", None, 123,
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, case_id in enumerate(invalid_ids):
                with self.subTest(case_id=case_id):
                    target = Path(directory) / f"case-{index}"
                    shutil.copytree(REFERENCE_CASE, target)
                    metadata = target / "metadata.yaml"
                    value = json.dumps(case_id) if isinstance(case_id, str) else "null" if case_id is None else str(case_id)
                    metadata.write_text(
                        metadata.read_text(encoding="utf-8").replace("case_id: case_001", f"case_id: {value}"),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(CaseValidationError, "filesystem-safe identifier"):
                        load_case(target)

    def test_reuses_small_unchanged_case_parse_and_invalidates_on_file_change(self):
        from fcapsule.io import case_loader

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "case"
            shutil.copytree(REFERENCE_CASE, target)
            with patch.object(case_loader, "_CASE_CACHE", None), patch.object(
                case_loader, "_validate_metrics", wraps=case_loader._validate_metrics
            ) as validate_metrics:
                first = load_case(target)
                second = load_case(target)
                self.assertIs(first, second)
                self.assertEqual(validate_metrics.call_count, 1)

                notes = target / "expected_notes.md"
                notes.write_text(notes.read_text(encoding="utf-8") + "\nUpdated note.\n", encoding="utf-8")
                third = load_case(target)
                self.assertIsNot(second, third)
                self.assertEqual(validate_metrics.call_count, 2)

    def test_rejects_case_input_over_configured_total_byte_limit(self):
        from fcapsule.io import case_loader

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "case"
            shutil.copytree(REFERENCE_CASE, target)
            with (target / "expected_notes.md").open("a", encoding="utf-8") as handle:
                handle.write("x" * 64)
            with patch.object(case_loader, "MAX_CASE_INPUT_BYTES", 100):
                with self.assertRaisesRegex(CaseValidationError, "total limit is 100 bytes"):
                    load_case(target)

    def test_loader_reports_log_message_and_capture_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "case"
            shutil.copytree(REFERENCE_CASE, target)
            logs_path = target / "opensearch_logs.json"
            data = json.loads(logs_path.read_text(encoding="utf-8"))
            data["hits"][0]["message_truncated"] = True
            data["capture"] = {
                "status": "partial",
                "unavailable_segments": [{"segment": "baseline", "reason": "response_byte_limit"}],
            }
            logs_path.write_text(json.dumps(data), encoding="utf-8")

            bundle = load_case(target)

            self.assertTrue(any("message(s) were truncated" in warning for warning in bundle.warnings))
            self.assertTrue(any("capture is partial" in warning and "baseline" in warning for warning in bundle.warnings))

    def test_read_case_json_enforces_the_configured_input_cap(self):
        from fcapsule.io import case_loader

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            path.write_text('{"series":[]}', encoding="utf-8")
            self.assertEqual(case_loader.read_case_json(path), {"series": []})
            with patch.object(case_loader, "MAX_CASE_INPUT_BYTES", 4):
                with self.assertRaisesRegex(CaseValidationError, "remaining case input limit of 4 bytes"):
                    case_loader.read_case_json(path)


if __name__ == "__main__":
    unittest.main()
