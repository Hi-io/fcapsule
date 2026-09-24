import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fcapsule.control_plane import ControlPlane
from fcapsule.reasoning.llm_client import LLMUnavailableError
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
            self.assertEqual(config["provider"], "deepseek")
            self.assertEqual(config["model"], "deepseek-v4-pro")
            self.assertEqual(config["max_total_tokens"], 12000)
            self.assertEqual(config["max_prompt_tokens"], 3200)
            self.assertEqual(config["max_checks"], 1)
            self.assertEqual(config["max_tokens"], 3600)

    def test_explicit_provider_selection_persists_and_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "existing-deepseek-key", "OPENROUTER_API_KEY": "", "FCAPSULE_LLM_PROVIDER": ""},
        ):
            plane = ControlPlane(Path(directory) / "state")
            config = plane.update_ai_configuration({"provider": "openrouter"})

            self.assertEqual(config["provider"], "openrouter")
            self.assertEqual(config["model"], "deepseek/deepseek-v4-pro-0813")
            self.assertFalse(config["api_key_configured"])
            self.assertEqual(config["capability"]["status"], "not_configured")
            self.assertEqual(plane.ai_configuration()["provider"], "openrouter")
            saved = json.loads((Path(directory) / "state" / "ai-settings.json").read_text())
            self.assertEqual(saved["provider"], "openrouter")
            self.assertNotIn("existing-deepseek-key", json.dumps(saved))

    def test_provider_specific_key_canary_uses_openrouter_and_saves_only_after_success(self):
        class _CoreClient:
            instances = []

            def __init__(self, api_key, timeout_seconds):
                self.api_key = api_key
                self.instances.append(self)

            def chat(self, request):
                self.request = request
                return {"content": '{"status":"ok"}', "usage": {"total_tokens": 2}}

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "existing-deepseek-key", "OPENROUTER_API_KEY": "", "FCAPSULE_LLM_PROVIDER": ""},
        ), patch("fcapsule.control_plane.OpenRouterChatClient", _CoreClient):
            plane = ControlPlane(Path(directory) / "state")
            config = plane.update_ai_configuration({"provider": "openrouter", "api_key": "candidate-openrouter-key-123"})

            self.assertEqual(config["provider"], "openrouter")
            self.assertEqual(config["capability"]["status"], "ready")
            self.assertTrue(config["api_key_configured"])
            self.assertEqual(_CoreClient.instances[0].api_key, "candidate-openrouter-key-123")
            self.assertTrue(_CoreClient.instances[0].request.json_output)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "existing-deepseek-key")
            self.assertNotIn("candidate-openrouter-key-123", json.dumps(config))
            saved = json.loads((Path(directory) / "state" / "ai-settings.json").read_text())
            self.assertNotIn("candidate-openrouter-key-123", json.dumps(saved))

            media_model = plane.media_configuration()["vision"]["model"]
            plane._set_capability(
                "media_vision_capability", "candidate-openrouter-key-123", media_model, "ready", "validated",
            )
            self.assertEqual(plane.media_configuration()["vision"]["capability"]["status"], "ready")
            rotated = plane.update_ai_configuration({"api_key": "replacement-openrouter-key-456"})
            self.assertEqual(rotated["capability"]["status"], "ready")
            self.assertEqual(
                plane.media_configuration()["vision"]["capability"]["status"], "not_validated",
            )

    def test_blank_openrouter_key_save_preserves_configured_credential(self):
        class _CoreClient:
            def __init__(self, api_key, timeout_seconds):
                self.api_key = api_key

            def chat(self, request):
                return {"content": '{"status":"ok"}', "usage": {"total_tokens": 1}}

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": "", "FCAPSULE_LLM_PROVIDER": ""},
        ), patch("fcapsule.control_plane.OpenRouterChatClient", _CoreClient):
            plane = ControlPlane(Path(directory) / "state")
            plane.update_ai_configuration({"provider": "openrouter", "api_key": "saved-openrouter-key-123"})

            config = plane.update_ai_configuration({"provider": "openrouter", "api_key": ""})

            self.assertEqual(os.environ["OPENROUTER_API_KEY"], "saved-openrouter-key-123")
            self.assertTrue(config["api_key_configured"])
            self.assertIn("OPENROUTER_API_KEY=saved-openrouter-key-123", (Path(directory) / "state" / ".env").read_text())

    def test_failed_openrouter_core_canary_does_not_replace_existing_key_or_provider(self):
        class _FailingCoreClient:
            def __init__(self, api_key, timeout_seconds):
                self.api_key = api_key

            def chat(self, request):
                raise LLMUnavailableError("OpenRouter API returned HTTP 402: insufficient credit")

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "existing-deepseek-key", "OPENROUTER_API_KEY": "existing-router-key", "FCAPSULE_LLM_PROVIDER": ""},
        ), patch("fcapsule.control_plane.OpenRouterChatClient", _FailingCoreClient):
            plane = ControlPlane(Path(directory) / "state")
            current = plane.ai_configuration()
            plane._set_capability(
                "ai_core_capability", "existing-deepseek-key", current["model"], "ready",
                "Existing DeepSeek key validated.", provider="deepseek",
            )
            with self.assertRaisesRegex(ValueError, "not saved"):
                plane.update_ai_configuration({"provider": "openrouter", "api_key": "candidate-openrouter-key-123"})

            self.assertEqual(os.environ["OPENROUTER_API_KEY"], "existing-router-key")
            self.assertEqual(plane.ai_configuration()["provider"], "deepseek")
            self.assertEqual(plane.ai_configuration()["capability"]["status"], "ready")
            self.assertEqual(plane.ai_configuration()["capability"]["message"], "Existing DeepSeek key validated.")
            saved_env = Path(directory) / "state" / ".env"
            self.assertFalse(saved_env.exists())

    def test_persisted_provider_selection_takes_precedence_over_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": "", "FCAPSULE_LLM_PROVIDER": "openrouter"},
        ):
            plane = ControlPlane(Path(directory) / "state")
            self.assertEqual(plane.ai_configuration()["provider"], "openrouter")
            plane.update_ai_configuration({"provider": "deepseek", "model": "deepseek-v4-flash"})
            self.assertEqual(plane.ai_configuration()["provider"], "deepseek")
            self.assertEqual(plane.ai_configuration()["model"], "deepseek-v4-flash")

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
