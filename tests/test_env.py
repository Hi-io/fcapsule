import os
import tempfile
import unittest
from pathlib import Path

from fcapsule.env import load_env_file, write_env_value


class EnvTests(unittest.TestCase):
    def test_loads_env_without_overriding_existing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("DEEPSEEK_API_KEY=from-file\nFCAPSULE_TEST_QUOTED='ok value'\n", encoding="utf-8")
            original = os.environ.get("DEEPSEEK_API_KEY")
            os.environ["DEEPSEEK_API_KEY"] = "from-shell"
            try:
                self.assertEqual(load_env_file(env_path), env_path.resolve())
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-shell")
                self.assertEqual(os.environ["FCAPSULE_TEST_QUOTED"], "ok value")
            finally:
                if original is None:
                    os.environ.pop("DEEPSEEK_API_KEY", None)
                else:
                    os.environ["DEEPSEEK_API_KEY"] = original
                os.environ.pop("FCAPSULE_TEST_QUOTED", None)

    def test_writes_and_replaces_one_local_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            original = os.environ.get("DEEPSEEK_API_KEY")
            try:
                write_env_value(env_path, "DEEPSEEK_API_KEY", "test-local-secret")
                self.assertEqual(env_path.read_text(encoding="utf-8"), "DEEPSEEK_API_KEY=test-local-secret\n")
                write_env_value(env_path, "DEEPSEEK_API_KEY", "replacement-secret")
                self.assertEqual(env_path.read_text(encoding="utf-8"), "DEEPSEEK_API_KEY=replacement-secret\n")
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "replacement-secret")
            finally:
                if original is None:
                    os.environ.pop("DEEPSEEK_API_KEY", None)
                else:
                    os.environ["DEEPSEEK_API_KEY"] = original


if __name__ == "__main__":
    unittest.main()
