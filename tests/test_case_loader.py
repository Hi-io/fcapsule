import json
import shutil
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
