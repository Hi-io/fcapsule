import unittest
from unittest.mock import patch

from fcapsule.adapters.transport import JsonTransport, ResponseTooLargeError


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.read_size = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, size=-1):
        self.read_size = size
        return self.payload if size < 0 else self.payload[:size]


class JsonTransportTests(unittest.TestCase):
    def test_optional_response_limit_reads_at_most_one_byte_over_cap(self):
        response = FakeResponse(b'{"x":1}')
        transport = JsonTransport("http://source")

        with patch("fcapsule.adapters.transport.urlopen", return_value=response):
            with self.assertRaisesRegex(ResponseTooLargeError, "6-byte limit"):
                transport.request("/search", max_response_bytes=6)

        self.assertEqual(response.read_size, 7)

    def test_response_limit_is_optional_and_accepts_payload_at_cap(self):
        response = FakeResponse(b'{"x":1}')
        transport = JsonTransport("http://source")

        with patch("fcapsule.adapters.transport.urlopen", return_value=response):
            self.assertEqual(transport.request("/search", max_response_bytes=7), {"x": 1})
        self.assertEqual(response.read_size, 8)

    def test_invalid_response_limit_is_rejected(self):
        transport = JsonTransport("http://source")
        with self.assertRaisesRegex(ValueError, "must be positive"):
            transport.request("/search", max_response_bytes=0)


if __name__ == "__main__":
    unittest.main()
