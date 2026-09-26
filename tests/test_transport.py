import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError

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

        with patch.object(transport.opener, "open", return_value=response):
            with self.assertRaisesRegex(ResponseTooLargeError, "6-byte limit"):
                transport.request("/search", max_response_bytes=6)

        self.assertEqual(response.read_size, 7)

    def test_response_limit_is_optional_and_accepts_payload_at_cap(self):
        response = FakeResponse(b'{"x":1}')
        transport = JsonTransport("http://source")

        with patch.object(transport.opener, "open", return_value=response):
            self.assertEqual(transport.request("/search", max_response_bytes=7), {"x": 1})
        self.assertEqual(response.read_size, 8)

    def test_invalid_response_limit_is_rejected(self):
        transport = JsonTransport("http://source")
        with self.assertRaisesRegex(ValueError, "must be positive"):
            transport.request("/search", max_response_bytes=0)

    def test_redirects_are_not_followed_with_source_credentials(self):
        requests = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                self.send_response(302 if self.path == "/start" else 200)
                if self.path == "/start":
                    self.send_header("Location", "/landing")
                else:
                    self.send_header("Content-Type", "application/json")
                    self.wfile.write(b"{}")
                self.end_headers()

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            transport = JsonTransport(f"http://127.0.0.1:{server.server_port}", username="user", password="secret")
            with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
                transport.request("/start")
            self.assertEqual([path for path, _ in requests], ["/start"])
            self.assertTrue(requests[0][1].startswith("Basic "))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
