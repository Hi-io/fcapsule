import tempfile
import unittest
from pathlib import Path

from scripts.capture_demo_incident import capture


class DemoCaptureTests(unittest.TestCase):
    def test_demo_failure_fires_alert_and_writes_valid_case(self):
        with tempfile.TemporaryDirectory() as directory:
            result = capture(Path(directory) / "case", healthy_requests=12, failing_requests=8)
            self.assertEqual(result["alert_status"], "firing")
            self.assertGreater(result["final_error_rate"], 0.25)
            self.assertGreater(result["captured_logs"], 20)


if __name__ == "__main__":
    unittest.main()
