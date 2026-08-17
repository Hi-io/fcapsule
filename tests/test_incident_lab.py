import tempfile
import unittest
from pathlib import Path

from demo.incident_lab import SimulationConfig, run_simulation
from fcapsule.io.case_loader import load_case


class IncidentLabTests(unittest.TestCase):
    def test_complex_incident_generates_multisignal_case(self):
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case"
            result = run_simulation(
                case_dir,
                SimulationConfig(baseline_requests=30, incident_requests=60, concurrency=16),
            )
            bundle = load_case(case_dir)
            self.assertEqual(result["alert_count"], 3)
            self.assertGreater(result["log_count"], 100)
            self.assertGreaterEqual(result["metric_series_count"], 18)
            self.assertGreater(result["retry_amplification"], 1.25)
            self.assertGreater(result["error_rate"], 0.12)
            self.assertEqual(result["pool_peak_utilization"], 1.0)
            self.assertTrue(result["trace_access"]["available"])
            self.assertFalse(result["trace_access"]["raw_spans_retained"])
            self.assertEqual(len(bundle.alerts), 3)


if __name__ == "__main__":
    unittest.main()
