"""Dependency-light local web application for FCAPSule operations."""

from __future__ import annotations

import json
import hmac
import mimetypes
import os
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from fcapsule.control_plane import ControlPlane


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FCAPSule</title>
  <link rel="icon" href="/assets/icons/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <a class="skip-link" href="#app">Skip to content</a>
  <header class="product-bar">
    <a class="wordmark" href="/console" aria-label="FCAPSule"><span class="brand-mark" aria-hidden="true"><span class="ui-icon" data-icon="scan-line"></span></span><span class="brand-name">FCAPSule</span></a>
    <nav aria-label="Primary">
      <a href="/console" data-nav="console"><span class="ui-icon" data-icon="activity" aria-hidden="true"></span>Operations</a>
      <a href="/targets" data-nav="targets"><span class="ui-icon" data-icon="network" aria-hidden="true"></span>Targets</a>
      <a href="/patterns" data-nav="patterns"><span class="ui-icon" data-icon="layers" aria-hidden="true"></span>Patterns</a>
      <a href="/estima" data-nav="estima"><span class="ui-icon" data-icon="network" aria-hidden="true"></span>Estima</a>
      <a href="/settings" data-nav="settings"><span class="ui-icon" data-icon="settings-2" aria-hidden="true"></span>Settings</a>
    </nav>
    <div class="system-state" role="status"><i></i><span id="system-state">Connecting</span></div>
  </header>
  <main id="app" tabindex="-1" aria-busy="true"><div class="boot">
    <div class="page-head boot-page-head"><h1 id="boot-title">FCAPSule</h1><div class="boot-heading" role="status" aria-live="polite"><span class="boot-spinner" aria-hidden="true"></span><span id="boot-label">Loading view</span></div></div>
    <div class="boot-content" aria-hidden="true"><div class="boot-toolbar"><span></span><span></span></div><div class="boot-placeholder"><div><span></span><span></span></div><div><span></span><span></span></div><div><span></span><span></span></div><div><span></span><span></span></div></div></div>
  </div></main>
  <script src="/assets/app.js"></script>
</body>
</html>"""


ASSET_ROOT = Path(__file__).with_name("assets")
CSS = "\n".join((ASSET_ROOT / name).read_text(encoding="utf-8") for name in ("app.css", "visual.css", "estima.css"))
JS = "\n".join((ASSET_ROOT / name).read_text(encoding="utf-8") for name in ("estima.js", "app.js"))
ICONS = {path.name: path.read_text(encoding="utf-8") for path in (ASSET_ROOT / "icons").glob("*.svg")}
STREAM_CHUNK_BYTES = 128 * 1024
JSON_SPOOL_MEMORY_BYTES = 1024 * 1024


def _canonical_memory_path(path: str) -> str:
    """Keep local Atlas URLs as aliases while making Estima the public route."""
    if path == "/api/settings/atlas":
        return "/api/settings/estima"
    if path == "/api/atlas" or path.startswith("/api/atlas/"):
        return path.replace("/api/atlas", "/api/estima", 1)
    return path


def _estima_client(control_plane: ControlPlane, operation: str):
    configured_client = getattr(control_plane, "estima_client", None) or getattr(control_plane, "atlas_client", None)
    if configured_client:
        try:
            return configured_client(operation)
        except Exception:
            return None
    try:
        from fcapsule.estima_client import estima_client_from_env
        return estima_client_from_env(operation=operation)
    except ImportError:
        return None
    except Exception:
        return None


def _estima_configuration(control_plane: ControlPlane) -> dict[str, Any]:
    read = getattr(control_plane, "estima_configuration", None) or getattr(control_plane, "atlas_configuration")
    return read()


def _update_estima_configuration(control_plane: ControlPlane, payload: dict[str, Any]) -> dict[str, Any]:
    update = getattr(control_plane, "update_estima_configuration", None) or getattr(control_plane, "update_atlas_configuration")
    return update(payload)


class FCAPSuleHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], control_plane: ControlPlane) -> None:
        self.control_plane = control_plane
        super().__init__(address, FCAPSuleHandler)

    def server_close(self) -> None:
        self.control_plane.investigator.stopping = True
        publisher = getattr(self.control_plane, "estima_publisher", None) or getattr(self.control_plane, "atlas_publisher", None)
        if publisher is not None:
            publisher.shutdown(drain=True, timeout=7.0)
        self.control_plane.stop_live_monitoring()
        self.control_plane.briefing_executor.shutdown(wait=False, cancel_futures=True)
        self.control_plane.evidence.shutdown(wait=False)
        super().server_close()


class FCAPSuleHandler(BaseHTTPRequestHandler):
    server: FCAPSuleHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        with tempfile.SpooledTemporaryFile(max_size=JSON_SPOOL_MEMORY_BYTES, mode="w+b") as body:
            encoder = json.JSONEncoder(ensure_ascii=True)
            for chunk in encoder.iterencode(payload):
                body.write(chunk.encode("utf-8"))
            length = body.tell()
            body.seek(0)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._copy_stream(body)

    def _copy_stream(self, source: Any) -> None:
        while chunk := source.read(STREAM_CHUNK_BYTES):
            self.wfile.write(chunk)

    def _capsule_json(self, payload: dict[str, Any]) -> None:
        capsule_path = Path(payload["capsule_path"])
        prefix = ("{\"record\": " + json.dumps(payload["record"], ensure_ascii=True)
                  + ", \"capsule\": ").encode("utf-8")
        suffix = (", \"comparison\": " + json.dumps(payload["comparison"], ensure_ascii=True) + "}").encode("utf-8")
        length = len(prefix) + capsule_path.stat().st_size + len(suffix)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(prefix)
        with capsule_path.open("rb") as capsule:
            self._copy_stream(capsule)
        self.wfile.write(suffix)

    def _file_response(
        self,
        file_path: Path,
        content_type: str,
        *,
        no_store: bool = False,
        nosniff: bool = False,
        attachment_filename: str | None = None,
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        if nosniff:
            self.send_header("X-Content-Type-Options", "nosniff")
        if attachment_filename:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment_filename}"')
        self.end_headers()
        with file_path.open("rb") as source:
            self._copy_stream(source)

    def _text(self, body: str, content_type: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _payload(self, maximum_bytes: int = 512 * 1024) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        if length < 0 or length > maximum_bytes:
            raise ValueError("Request body exceeds the permitted size")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _atlas_unavailable(self) -> None:
        try:
            config = _estima_configuration(self.server.control_plane)
        except AttributeError:
            config = {}
        if not config.get("url"):
            status, message = "not_configured", "Add the Estima service URL in Settings."
        elif not config.get("read_enabled"):
            status, message = "disabled", "Estima reads are disabled in Settings."
        else:
            status, message = "unavailable", "Estima client is unavailable in this FCAPSule build."
        self._json({"status": status, "error": message}, HTTPStatus.SERVICE_UNAVAILABLE)

    def _atlas_failure(self, exc: Exception) -> None:
        self._json({
            "status": "unavailable",
            "error": "Estima request failed. Check the saved URL, access token, and service availability.",
        }, HTTPStatus.SERVICE_UNAVAILABLE)

    def do_GET(self) -> None:  # noqa: N802
        path = _canonical_memory_path(urlparse(self.path).path)
        if path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/console")
            self.end_headers()
            return
        if path in {"/console", "/targets", "/patterns", "/atlas", "/estima", "/settings"} or path.startswith(("/atlas/", "/estima/")):
            self._text(HTML, "text/html; charset=utf-8")
            return
        if path == "/assets/app.css":
            self._text(CSS, "text/css; charset=utf-8")
            return
        if path == "/assets/app.js":
            self._text(JS, "text/javascript; charset=utf-8")
            return
        if path.startswith("/assets/icons/") and path.removeprefix("/assets/icons/") in ICONS:
            self._text(ICONS[path.removeprefix("/assets/icons/")], "image/svg+xml")
            return
        if path == "/api/state":
            self._json(self.server.control_plane.snapshot())
            return
        if path == "/healthz":
            self._json({"status": "ok"})
            return
        if path.startswith("/api/episodes/") and path.endswith("/investigation"):
            episode_id = unquote(path.removeprefix("/api/episodes/").removesuffix("/investigation").rstrip("/"))
            if not self.server.control_plane.store.get_episode(episode_id):
                self._json({"error": "Episode not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json(self.server.control_plane.investigator.read(episode_id))
            return
        if path == "/api/settings/ai":
            self._json(self.server.control_plane.ai_configuration())
            return
        if path == "/api/settings/media":
            self._json(self.server.control_plane.media_configuration())
            return
        if path == "/api/settings/general":
            self._json(self.server.control_plane.general_configuration())
            return
        if path == "/api/settings/sources":
            self._json(self.server.control_plane.source_configuration())
            return
        if path == "/api/settings/estima":
            self._json(_estima_configuration(self.server.control_plane))
            return
        if path == "/api/estima/patterns":
            query = parse_qs(urlparse(self.path).query)
            cluster = (query.get("cluster") or query.get("scope") or [None])[0]
            scope = {"cluster": cluster} if cluster else None
            search = (query.get("query") or [None])[0]
            before = (query.get("before") or [None])[0]
            try:
                limit = max(1, min(50, int((query.get("limit") or ["20"])[0])))
            except ValueError:
                self._json({"error": "limit must be a number", "status": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                return
            client = _estima_client(self.server.control_plane, "read")
            if client is None:
                self._atlas_unavailable()
                return
            try:
                self._json(client.list_patterns(scope=scope, query=search, limit=limit, before=before))
            except Exception as exc:
                self._atlas_failure(exc)
            return
        if path.startswith("/api/estima/patterns/"):
            pattern_id = unquote(path.removeprefix("/api/estima/patterns/").rstrip("/"))
            client = _estima_client(self.server.control_plane, "read")
            if client is None:
                self._atlas_unavailable()
                return
            try:
                result = client.get_pattern(pattern_id)
                if result is None:
                    self._json({"error": "Pattern not found", "status": "not_found"}, HTTPStatus.NOT_FOUND)
                else:
                    self._json(result)
            except Exception as exc:
                self._atlas_failure(exc)
            return
        if path.startswith("/api/estima/cases/"):
            case_id = unquote(path.removeprefix("/api/estima/cases/").rstrip("/"))
            client = _estima_client(self.server.control_plane, "read")
            if client is None:
                self._atlas_unavailable()
                return
            try:
                result = client.get_case(case_id)
                if result is None:
                    self._json({"error": "Case not found", "status": "not_found"}, HTTPStatus.NOT_FOUND)
                else:
                    self._json(result)
            except Exception as exc:
                self._atlas_failure(exc)
            return
        if path.startswith("/api/episodes/") and path.endswith("/evidence"):
            episode_id = unquote(path.removeprefix("/api/episodes/").removesuffix("/evidence").rstrip("/"))
            if not self.server.control_plane.store.get_episode(episode_id):
                self._json({"error": "Episode not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json(self.server.control_plane.evidence.list(episode_id))
            return
        if path.startswith("/api/evidence/") and path.endswith("/file"):
            attachment_id = unquote(path.removeprefix("/api/evidence/").removesuffix("/file").rstrip("/"))
            asset = self.server.control_plane.evidence.file(attachment_id)
            if not asset:
                self._json({"error": "Evidence file not found"}, HTTPStatus.NOT_FOUND)
            else:
                file_path, mime_type = asset
                self._file_response(
                    file_path, mime_type, no_store=True, nosniff=True,
                    attachment_filename=file_path.name if mime_type.startswith("text/plain") else None,
                )
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
            payload = self.server.control_plane.capsule_artifact_payload(capsule_id)
            if payload is None:
                self._json({"error": "Capsule not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._capsule_json(payload)
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
        self._file_response(artifact, mimetypes.guess_type(name)[0] or "application/octet-stream")

    def do_POST(self) -> None:  # noqa: N802
        path = _canonical_memory_path(urlparse(self.path).path)
        try:
            if path == "/api/webhooks/grafana":
                config = self.server.control_plane.source_configuration()
                if not config.get("grafana_webhook_enabled"):
                    self._json({"error": "Grafana webhook is disabled"}, HTTPStatus.NOT_FOUND)
                    return
                token = os.environ.get("FCAPSULE_GRAFANA_WEBHOOK_TOKEN", "")
                supplied = self.headers.get("Authorization", "")
                if not token or not hmac.compare_digest(supplied, f"Bearer {token}"):
                    self._json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._json({"error": "Expected application/json"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                    return
                self._json(self.server.control_plane.receive_grafana_webhook(self._payload(256 * 1024)), HTTPStatus.ACCEPTED)
                return
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
            if path == "/api/settings/ai/validate":
                self._json(self.server.control_plane.validate_ai_configuration())
                return
            if path == "/api/settings/media":
                self._json(self.server.control_plane.update_media_configuration(self._payload()))
                return
            if path == "/api/settings/media/validate":
                self._json(self.server.control_plane.validate_media_configuration())
                return
            if path == "/api/settings/general":
                self._json(self.server.control_plane.update_general_configuration(self._payload()))
                return
            if path == "/api/settings/sources":
                self._json(self.server.control_plane.update_source_configuration(self._payload()))
                return
            if path == "/api/settings/estima":
                self._json(_update_estima_configuration(self.server.control_plane, self._payload()))
                return
            if path == "/api/estima/search":
                payload = self._payload()
                query = str(payload.get("query") or "").strip()
                if not query:
                    self._json({"error": "Enter a search term", "status": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    limit = max(1, min(10, int(payload.get("limit", 10))))
                except (TypeError, ValueError):
                    self._json({"error": "limit must be a number", "status": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                    return
                scope = payload.get("scope")
                if isinstance(scope, str):
                    scope = {"cluster": scope.strip()} if scope.strip() else None
                if scope is not None and (not isinstance(scope, dict) or any(key not in {"cluster", "namespace", "service", "workload", "cnfc_id", "vnfc_id"} for key in scope)):
                    self._json({"error": "scope must contain supported scope fields", "status": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                    return
                client = _estima_client(self.server.control_plane, "read")
                if client is None:
                    self._atlas_unavailable()
                    return
                try:
                    self._json(client.search(
                        scope=scope,
                        query=query,
                        limit=limit,
                        before=str(payload.get("before") or "").strip() or None,
                    ))
                except Exception as exc:
                    self._atlas_failure(exc)
                return
            if path == "/api/estima/retry-failed":
                retry = getattr(self.server.control_plane, "retry_estima_publications", None) or getattr(self.server.control_plane, "retry_atlas_publications", None)
                if retry is None:
                    self._json({"error": "Estima retry is unavailable in this FCAPSule build"}, HTTPStatus.NOT_IMPLEMENTED)
                    return
                self._json(retry(limit=100), HTTPStatus.ACCEPTED)
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
            if path.startswith("/api/episodes/") and path.endswith("/investigation/update"):
                episode_id = unquote(path.removeprefix("/api/episodes/").removesuffix("/investigation/update").rstrip("/"))
                self._json(self.server.control_plane.update_investigation_with_evidence(episode_id), HTTPStatus.ACCEPTED)
                return
            if path.startswith("/api/episodes/") and path.endswith("/source-review"):
                episode_id = unquote(path.removeprefix("/api/episodes/").removesuffix("/source-review").rstrip("/"))
                payload = self._payload()
                self._json(self.server.control_plane.start_source_disconnected_review(episode_id, str(payload.get("question") or "")), HTTPStatus.ACCEPTED)
                return
            if path.startswith("/api/related-groups/") and path.endswith("/separate"):
                parts = path.strip("/").split("/")
                if len(parts) != 6 or parts[0:2] != ["api", "related-groups"] or parts[3] != "episodes":
                    self._json({"error": "Invalid related group path"}, HTTPStatus.BAD_REQUEST)
                    return
                self._json(self.server.control_plane.separate_related_episode(unquote(parts[2]), unquote(parts[4])))
                return
            if path.startswith("/api/episodes/") and path.endswith("/investigation"):
                episode_id = unquote(path.removeprefix("/api/episodes/").removesuffix("/investigation").rstrip("/"))
                payload = self._payload()
                self._json(
                    self.server.control_plane.investigator.start(
                        episode_id,
                        retry=True,
                        primary_incident_id=str(payload.get("incident_id") or "") or None,
                    ),
                    HTTPStatus.ACCEPTED,
                )
                return
            if path.startswith("/api/episodes/") and path.endswith("/evidence"):
                episode_id = unquote(path.removeprefix("/api/episodes/").removesuffix("/evidence").rstrip("/"))
                self._json(self.server.control_plane.submit_evidence(episode_id, self._payload(12 * 1024 * 1024)), HTTPStatus.ACCEPTED)
                return
            if path.startswith("/api/evidence/") and path.endswith("/correction"):
                attachment_id = unquote(path.removeprefix("/api/evidence/").removesuffix("/correction").rstrip("/"))
                self._json(self.server.control_plane.correct_evidence(attachment_id, self._payload()))
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
            if path.startswith("/api/evidence/"):
                attachment_id = unquote(path.removeprefix("/api/evidence/").rstrip("/"))
                self.server.control_plane.remove_evidence(attachment_id)
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
    control_plane.investigator.resume()
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
