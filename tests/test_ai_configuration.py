import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fcapsule.control_plane import ControlPlane
from fcapsule.reasoning.openrouter import OpenRouterError


class _MediaClient:
    def __init__(self, api_key, timeout_seconds):
        self.api_key = api_key

    def validate_vision(self, model):
        return {"usage": {"total_tokens": 2}, "model": model}

    def validate_asr(self, model):
        return {"usage": {"total_tokens": 1}, "model": model}


class _FailingMediaClient(_MediaClient):
    def validate_vision(self, model):
        raise OpenRouterError("HTTP 401 invalid credentials")

    def validate_asr(self, model):
        raise OpenRouterError("HTTP 401 invalid credentials")


class AiConfigurationTests(unittest.TestCase):
    def test_budget_settings_are_bounded_and_available_to_investigator(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""}):
            plane = ControlPlane(Path(directory) / "state")
            config = plane.update_ai_configuration(
                {"model": "deepseek-v4-flash", "max_tokens": 900, "max_total_tokens": 12000,
                 "max_prompt_tokens": 2100, "max_checks": 1}
            )
            self.assertEqual(config["max_tokens"], 900)
            self.assertEqual(config["max_total_tokens"], 12000)
            self.assertEqual(config["max_prompt_tokens"], 2100)
            self.assertEqual(config["max_checks"], 1)
            self.assertEqual(config["capability"]["status"], "not_configured")

    def test_media_key_is_persisted_only_after_a_capability_validates(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""}):
            plane = ControlPlane(Path(directory) / "state")
            with patch("fcapsule.control_plane.OpenRouterClient", _MediaClient):
                config = plane.update_media_configuration({"api_key": "candidate-media-key-123"})
            self.assertTrue(config["api_key_configured"])
            self.assertEqual(config["vision"]["capability"]["status"], "ready")
            self.assertEqual(config["audio"]["capability"]["status"], "ready")
            self.assertNotIn("candidate-media-key-123", str(config))
            self.assertTrue((Path(directory) / "state" / ".env").is_file())

    def test_failed_replacement_does_not_replace_existing_media_credential(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": "existing-media-key"}):
            plane = ControlPlane(Path(directory) / "state")
            with patch("fcapsule.control_plane.OpenRouterClient", _FailingMediaClient):
                with self.assertRaisesRegex(ValueError, "not saved"):
                    plane.update_media_configuration({"api_key": "bad-media-key-123"})
            self.assertEqual(os.environ["OPENROUTER_API_KEY"], "existing-media-key")

    def test_media_submission_requires_core_and_specialist_validation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"DEEPSEEK_API_KEY": "core-key", "OPENROUTER_API_KEY": "media-key"}
        ):
            plane = ControlPlane(Path(directory) / "state")
            allowed, _ = plane.media_submission_allowed("vision")
            self.assertFalse(allowed)
            ai = plane.ai_configuration()
            media = plane.media_configuration()
            plane._set_capability("ai_core_capability", "core-key", ai["model"], "ready", "validated")
            plane._set_capability("media_vision_capability", "media-key", media["vision"]["model"], "ready", "validated")
            allowed, message = plane.media_submission_allowed("vision")
            self.assertTrue(allowed, message)


if __name__ == "__main__":
    unittest.main()
