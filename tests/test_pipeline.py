import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from fcapsule.pipeline import investigate_case
from tests.common import CASE_001


class PipelineRegressionTests(unittest.TestCase):
    def test_reference_case_generates_complete_grounded_capsule(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            result = investigate_case(CASE_001, output)
            expected = {"capsule.md", "capsule.json", "evidence.json", "evaluation.json", "baselines.json", "fcapsule_case_001.zip"}
            self.assertTrue(expected.issubset({path.name for path in output.iterdir()}))
            self.assertEqual(result["verified_hypotheses"], result["hypotheses"])
            evaluation = json.loads((output / "evaluation.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(evaluation["log_compression_ratio"], 0.9)
            self.assertGreaterEqual(evaluation["important_signal_preservation"], 0.9)
            self.assertEqual(evaluation["hypothesis_grounding_score"], 1.0)
            capsule = (output / "capsule.md").read_text(encoding="utf-8")
            self.assertIn("## 11. Retention Note", capsule)
            self.assertIn("does not assert a final root cause", capsule)

    def test_archive_excludes_raw_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            investigate_case(CASE_001, output)
            with ZipFile(output / "fcapsule_case_001.zip") as archive:
                names = set(archive.namelist())
            self.assertNotIn("opensearch_logs.json", names)
            self.assertEqual(names, {"capsule.md", "capsule.json", "evidence.json", "evaluation.json", "baselines.json"})


if __name__ == "__main__":
    unittest.main()
