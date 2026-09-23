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
    def test_default_investigation_budget_is_bounded_for_interactive_triage(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""}):
            plane = ControlPlane(Path(directory) / "state")
            config = plane.ai_configuration()
            self.assertEqual(config["max_total_tokens"], 12000)
            self.assertEqual(config["max_prompt_tokens"], 3200)
            self.assertEqual(config["max_checks"], 1)
            self.assertEqual(config["max_tokens"], 3600)

    def test_invalid_prompt_setting_uses_default_without_rewriting_saved_settings(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""}):
            plane = ControlPlane(Path(directory) / "state")
            for stored in ("", "invalid"):
                with self.subTest(stored=stored):
                    plane.store.set_setting("ai_max_prompt_tokens", stored)
                    config = plane.ai_configuration()
                    self.assertEqual(config["max_prompt_tokens"], 3200)
                    self.assertEqual(config["max_total_tokens"], 12000)
                    self.assertEqual(config["max_checks"], 1)
                    self.assertEqual(config["max_tokens"], 3600)
                    self.assertEqual(plane.store.get_setting("ai_max_prompt_tokens"), stored)

    def test_explicit_smaller_prompt_caps_and_existing_bounds_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""}):
            plane = ControlPlane(Path(directory) / "state")
            for requested, expected in ((2100, 2100), (1600, 1600), (1599, 1600), (12001, 12000)):
                with self.subTest(requested=requested):
                    config = plane.update_ai_configuration({"max_prompt_tokens": requested})
                    self.assertEqual(config["max_prompt_tokens"], expected)
                    self.assertEqual(plane.ai_configuration()["max_prompt_tokens"], expected)
                    self.assertEqual(config["max_total_tokens"], 12000)
                    self.assertEqual(config["max_checks"], 1)
                    self.assertEqual(config["max_tokens"], 3600)

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
            self.assertEqual(plane.update_ai_configuration({"max_tokens": 1000})["max_prompt_tokens"], 2100)

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
