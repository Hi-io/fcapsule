import base64
import json
import os
import struct
import unittest
from unittest.mock import patch

from fcapsule.reasoning.openrouter import OpenRouterClient


class _Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class OpenRouterClientTests(unittest.TestCase):
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
