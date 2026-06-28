import tempfile
import unittest
from pathlib import Path

from fcapsule.pipeline import investigate_case
from fcapsule.ui.demo_app import build_demo_summary, render_printout
from tests.common import CASE_001


class DemoUITests(unittest.TestCase):
    def test_demo_summary_and_printout_are_human_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            investigate_case(CASE_001, output)
            summary = build_demo_summary(CASE_001, output)
            self.assertEqual(summary["case"]["case_id"], "case_001")
            self.assertGreaterEqual(summary["pipeline"]["selected_evidence"], 3)
            self.assertIn("fault_events", summary["domains"])
            printout = render_printout(summary)
            self.assertIn("FCAPSule AI P1 demo summary", printout)
            self.assertIn("Log compression", printout)
            self.assertIn("LLM comparison", printout)


if __name__ == "__main__":
    unittest.main()
