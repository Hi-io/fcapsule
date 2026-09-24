import base64
import io
import json
import os
import struct
import urllib.error
import unittest
from unittest.mock import patch

from fcapsule.reasoning.llm_client import ChatRequest, LLMUnavailableError, OpenRouterChatClient as OpenRouterCoreChatClient
from fcapsule.reasoning.openrouter import OpenRouterClient


class _Response:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class OpenRouterClientTests(unittest.TestCase):
    def test_core_chat_request_preserves_json_and_token_accounting_contract(self):
        with patch(
            "fcapsule.reasoning.llm_client.urllib.request.urlopen",
            return_value=_Response({
                "id": "gen-1", "model": "deepseek/deepseek-v4-pro-0813",
                "choices": [{"message": {"content": '{"action":"finish"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 101, "completion_tokens": 23, "total_tokens": 124},
            }),
        ) as request:
            result = OpenRouterCoreChatClient(api_key="test-openrouter-key").chat(ChatRequest(
                model="deepseek/deepseek-v4-pro-0813",
                messages=[{"role": "user", "content": "Return bounded JSON."}],
                max_tokens=256,
                reasoning_effort="none",
                json_output=True,
            ))

        http_request = request.call_args.args[0]
        payload = json.loads(http_request.data.decode("utf-8"))
        self.assertEqual(http_request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(http_request.get_header("Authorization"), "Bearer test-openrouter-key")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["provider"], {"require_parameters": True})
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertEqual(payload["max_tokens"], 256)
        self.assertEqual(result["provider"], "openrouter")
        self.assertEqual(result["usage"]["total_tokens"], 124)
        self.assertEqual(result["content"], '{"action":"finish"}')

    def test_core_chat_rejects_malformed_json_and_empty_choices(self):
        with patch(
            "fcapsule.reasoning.llm_client.urllib.request.urlopen", return_value=_Response(b"not json"),
        ):
            with self.assertRaisesRegex(LLMUnavailableError, "invalid JSON"):
                OpenRouterCoreChatClient(api_key="test-openrouter-key").chat(ChatRequest("model", []))
        with patch(
            "fcapsule.reasoning.llm_client.urllib.request.urlopen", return_value=_Response({"choices": []}),
        ):
            with self.assertRaisesRegex(LLMUnavailableError, "no chat choice"):
                OpenRouterCoreChatClient(api_key="test-openrouter-key").chat(ChatRequest("model", []))

    def test_http_error_redacts_candidate_key(self):
        secret = "private-openrouter-test-key"
        error = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions", 402, "Payment Required", {},
            io.BytesIO(json.dumps({"error": "bad key " + secret}).encode("utf-8")),
        )
        with patch("fcapsule.reasoning.llm_client.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(LLMUnavailableError) as caught:
                OpenRouterCoreChatClient(api_key=secret).chat(ChatRequest("model", []))
        self.assertIn("HTTP 402", str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn("[redacted]", str(caught.exception))

    def test_audio_request_uses_provider_format_not_a_mime_field(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), patch(
            "fcapsule.reasoning.openrouter.urllib.request.urlopen",
            return_value=_Response({"text": "operator observation", "usage": {"total_tokens": 2}}),
        ) as request:
            client = OpenRouterClient()
            result = client.transcribe(b"small audio", "audio/wav", "qwen/qwen3-asr-0.6b")

        payload = json.loads(request.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(result["transcript"], "operator observation")
        self.assertEqual(payload["input_audio"]["format"], "wav")
        self.assertNotIn("mime_type", payload["input_audio"])
        self.assertEqual(payload["response_format"], "json")

    def test_visual_canary_is_a_provider_safe_32_pixel_png(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), patch(
            "fcapsule.reasoning.openrouter.urllib.request.urlopen",
            return_value=_Response({"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 2}}),
        ) as request:
            OpenRouterClient().validate_vision("qwen/qwen3-vl-8b-instruct")

        payload = json.loads(request.call_args.args[0].data.decode("utf-8"))
        url = payload["messages"][0]["content"][1]["image_url"]["url"]
        data = base64.b64decode(url.split(",", 1)[1])
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]), (32, 32))
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIn("at most 8 visible_text entries", payload["messages"][0]["content"][0]["text"])

    def test_visual_extraction_reserves_output_for_a_complete_json_response(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), patch(
            "fcapsule.reasoning.openrouter.urllib.request.urlopen",
            return_value=_Response({"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 2}}),
        ) as request:
            OpenRouterClient().visual_extract(b"small png", "image/png", "qwen/qwen3-vl-30b-a3b-instruct")

        payload = json.loads(request.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["max_tokens"], 800)


if __name__ == "__main__":
    unittest.main()
