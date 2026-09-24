import json
import tempfile
import unittest
from pathlib import Path

from fcapsule.io.output_writer import write_json


class OutputWriterTests(unittest.TestCase):
    def test_write_json_is_compact_and_round_trips(self):
        payload = {
            "evidence": [{"summary": "Repeated timeout observed", "references": ["E001", "Q001"]}],
            "unicode": "retained context",
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "artifact.json"
            write_json(path, payload)

            data = path.read_bytes()
            self.assertEqual(json.loads(data), payload)
            self.assertTrue(data.endswith(b"\n"))
            pretty_size = len((json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
            self.assertLess(len(data), pretty_size)


if __name__ == "__main__":
    unittest.main()
