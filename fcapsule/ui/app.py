"""Dependency-light local web application for FCAPSule operations."""

from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fcapsule.control_plane import ControlPlane


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FCAPSule</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <header class="product-bar">
    <a class="wordmark" href="/console"><span>FCAPS</span>ule</a>
    <nav aria-label="Primary">
      <a href="/console" data-nav="console">Operations</a>
      <a href="/targets" data-nav="targets">Targets</a>
      <a href="/settings" data-nav="settings">Settings</a>
    </nav>
    <div class="system-state"><i></i><span id="system-state">Ready</span></div>
  </header>
  <main id="app"><div class="boot">Loading FCAPSule...</div></main>
  <script src="/assets/app.js"></script>
</body>
</html>"""


ASSET_ROOT = Path(__file__).with_name("assets")
CSS = (ASSET_ROOT / "app.css").read_text(encoding="utf-8")
JS = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")


class FCAPSuleHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], control_plane: ControlPlane) -> None:
        self.control_plane = control_plane
        super().__init__(address, FCAPSuleHandler)

    def server_close(self) -> None:
        self.control_plane.stop_live_monitoring()
        self.control_plane.briefing_executor.shutdown(wait=False, cancel_futures=True)
        super().server_close()


class FCAPSuleHandler(BaseHTTPRequestHandler):
    server: FCAPSuleHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, body: str, content_type: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/console")
            self.end_headers()
            return
        if path in {"/console", "/targets", "/settings"}:
            self._text(HTML, "text/html; charset=utf-8")
            return
        if path == "/assets/app.css":
            self._text(CSS, "text/css; charset=utf-8")
            return
        if path == "/assets/app.js":
            self._text(JS, "text/javascript; charset=utf-8")
            return
        if path == "/api/state":
            self._json(self.server.control_plane.snapshot())
            return
        if path == "/healthz":
            self._json({"status": "ok"})
            return
        if path == "/api/settings/ai":
            self._json(self.server.control_plane.ai_configuration())
            return
        if path == "/api/settings/general":
            self._json(self.server.control_plane.general_configuration())
            return
        if path == "/api/settings/sources":
            self._json(self.server.control_plane.source_configuration())
            return
        if path.startswith("/api/incidents/") and path.endswith("/report"):
            incident_id = unquote(path.removeprefix("/api/incidents/").removesuffix("/report").rstrip("/"))
            payload = self.server.control_plane.incident_report_payload(incident_id)
            if payload is None:
                self._json({"error": "Incident not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json(payload)
            return
        if path.startswith("/api/capsules/"):
            capsule_id = unquote(path.removeprefix("/api/capsules/"))
            payload = self.server.control_plane.capsule_payload(capsule_id)
            if payload is None:
                self._json({"error": "Capsule not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json(payload)
            return
        if path.startswith("/artifacts/"):
            self._artifact(path)
            return
        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _artifact(self, path: str) -> None:
        parts = path.strip("/").split("/", 2)
        if len(parts) != 3:
            self._json({"error": "Invalid artifact path"}, HTTPStatus.BAD_REQUEST)
            return
        _, capsule_id, name = parts
        record = self.server.control_plane.store.get_capsule(unquote(capsule_id))
        if not record or Path(name).name != name:
            self._json({"error": "Artifact not found"}, HTTPStatus.NOT_FOUND)
            return
        artifact = Path(record["output_dir"]) / name
        if not artifact.is_file():
            self._json({"error": "Artifact not found"}, HTTPStatus.NOT_FOUND)
            return
        body = artifact.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/capsules":
                payload = self._payload()
                if not self.server.control_plane.start_capsule(payload.get("incident_id")):
                    self._json({"error": "Another job is already running"}, HTTPStatus.CONFLICT)
                else:
                    self._json({"ok": True}, HTTPStatus.ACCEPTED)
                return
            if path == "/api/settings/ai":
                self._json(self.server.control_plane.update_ai_configuration(self._payload()))
                return
            if path == "/api/settings/general":
                self._json(self.server.control_plane.update_general_configuration(self._payload()))
                return
            if path == "/api/settings/sources":
                self._json(self.server.control_plane.update_source_configuration(self._payload()))
                return
            if path == "/api/sources/test":
                result = self.server.control_plane.test_source_connections()
                self._json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if path == "/api/sources/sync":
                if not self.server.control_plane.start_source_sync():
                    self._json({"error": "Another job is already running"}, HTTPStatus.CONFLICT)
                else:
                    self._json({"ok": True}, HTTPStatus.ACCEPTED)
                return
            if path.startswith("/api/incidents/") and path.endswith("/briefing"):
                incident_id = unquote(path.removeprefix("/api/incidents/").removesuffix("/briefing").rstrip("/"))
                result = self.server.control_plane.start_ai_briefing(incident_id, retry=True)
                status = HTTPStatus.ACCEPTED if result.get("status") in {"queued", "running"} else HTTPStatus.OK
                self._json(result, status)
                return
            if path.startswith("/api/incidents/") and path.endswith("/archive"):
                incident_id = unquote(path.removeprefix("/api/incidents/").removesuffix("/archive").rstrip("/"))
                self._json(self.server.control_plane.set_incident_archived(incident_id, True))
                return
            if path.startswith("/api/incidents/") and path.endswith("/restore"):
                incident_id = unquote(path.removeprefix("/api/incidents/").removesuffix("/restore").rstrip("/"))
                self._json(self.server.control_plane.set_incident_archived(incident_id, False))
                return
            if path.startswith("/api/episodes/") and path.endswith("/archive"):
                episode_id = unquote(path.removeprefix("/api/episodes/").removesuffix("/archive").rstrip("/"))
                self._json(self.server.control_plane.set_episode_archived(episode_id, True))
                return
            if path.startswith("/api/episodes/") and path.endswith("/restore"):
                episode_id = unquote(path.removeprefix("/api/episodes/").removesuffix("/restore").rstrip("/"))
                self._json(self.server.control_plane.set_episode_archived(episode_id, False))
                return
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/episodes/"):
                episode_id = unquote(path.removeprefix("/api/episodes/").rstrip("/"))
                self.server.control_plane.delete_episode(episode_id)
                self._json({"ok": True})
                return
            if path.startswith("/api/incidents/"):
                incident_id = unquote(path.removeprefix("/api/incidents/").rstrip("/"))
                self.server.control_plane.delete_incident(incident_id)
                self._json({"ok": True})
                return
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)


def create_app_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    state_dir: str | Path = ".fcapsule",
) -> FCAPSuleHTTPServer:
    control_plane = ControlPlane(state_dir)
    server = FCAPSuleHTTPServer((host, port), control_plane)
    control_plane.resume_ai_briefings()
    control_plane.start_live_monitoring()
    return server


def serve_app(host: str = "127.0.0.1", port: int = 8765, state_dir: str | Path = ".fcapsule") -> None:
    server = create_app_server(host, port, state_dir)
    print(f"FCAPSule is running at http://{host}:{server.server_port}/console")
    print(f"Source targets: http://{host}:{server.server_port}/targets")
    print(f"Settings: http://{host}:{server.server_port}/settings")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
